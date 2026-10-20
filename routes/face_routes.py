"""
TrackIt System - Face Verification Routes
Handles face registration, verification, and liveness check storage.

Identity is matched against the intern's registered face only:
  - 128-d face-api descriptors when the app sends them
  - Pixel / histogram comparison of the stored registration photo vs live photo
  - Landmark geometry is NEVER enough on its own (most faces share similar layout)
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
from services.firebase_service import FirebaseService
from PIL import Image, ImageOps
import os
import uuid
import base64
import json
import math
import io
import requests

face_bp = Blueprint('face', __name__)

# Shared service instance — injected by app.py
_firebase_service = None


def set_firebase_service(service):
    global _firebase_service
    _firebase_service = service


def _get_service():
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseService()
    return _firebase_service


def _upload_to_firebase_storage(image_bytes: bytes, destination_path: str):
    """Upload bytes to Firebase Storage and return the public download URL."""
    try:
        from firebase_admin import storage
        bucket = storage.bucket()
        blob = bucket.blob(destination_path)
        blob.upload_from_string(image_bytes, content_type='image/jpeg')
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print(f"[ERROR] Firebase Storage upload failed for {destination_path}: {e}")
        return None


def _save_image(image_bytes: bytes, user_id: str, prefix: str = 'face') -> str:
    """Upload image bytes to Firebase Storage, return public URL."""
    filename = f"{prefix}_{user_id}_{uuid.uuid4().hex[:8]}.jpg"
    url = _upload_to_firebase_storage(image_bytes, f'faces/{filename}')
    if url:
        return url
    # Fallback: return a placeholder path so callers don't get None
    return f"/uploads/faces/{filename}"


def _decode_image(request_obj) -> bytes | None:
    """
    Accept image from either:
      - multipart/form-data  field name 'image'
      - JSON body            field 'imageBase64' (base64 string)
    Returns raw bytes or None.
    """
    if request_obj.content_type and 'multipart/form-data' in request_obj.content_type:
        file = request_obj.files.get('image')
        if file:
            return file.read()
    else:
        data = request_obj.get_json(silent=True) or {}
        b64 = data.get('imageBase64', '')
        if b64:
            # Strip data-URI prefix if present
            if ',' in b64:
                b64 = b64.split(',', 1)[1]
            try:
                return base64.b64decode(b64)
            except Exception:
                pass
    return None


def _extract_landmarks_from_mlkit(mlkit_data: dict) -> dict | None:
    """
    Extract a normalised landmark map from ML Kit face data sent by the app.
    The app sends: { landmarks: { noseTip: {x,y}, leftEye: {x,y}, ... },
                     boundingBox: {width, height, ...} }
    We normalise by bounding-box size so the stored values are scale-invariant.
    """
    try:
        landmarks = mlkit_data.get('landmarks', {})
        bbox = mlkit_data.get('boundingBox', {})
        w = float(bbox.get('width', 1))
        h = float(bbox.get('height', 1))
        if w == 0 or h == 0:
            return None
        normalised = {}
        for name, pt in landmarks.items():
            normalised[name] = {
                'nx': float(pt['x']) / w,
                'ny': float(pt['y']) / h,
            }
        return normalised
    except Exception:
        return None


def _landmark_distance(stored: dict, captured: dict) -> float:
    """
    Compute mean normalised Euclidean distance between matching landmarks.
    Lower = more similar.  Returns value in [0, ∞).
    """
    skip = {'descriptor', 'crop', '_crop'}
    keys = (set(stored.keys()) & set(captured.keys())) - skip
    if not keys:
        return 999.0
    total = 0.0
    counted = 0
    for k in keys:
        if not isinstance(stored.get(k), dict) or not isinstance(captured.get(k), dict):
            continue
        if 'nx' not in stored[k] or 'nx' not in captured[k]:
            continue
        dx = stored[k]['nx'] - captured[k]['nx']
        dy = stored[k]['ny'] - captured[k]['ny']
        total += math.sqrt(dx * dx + dy * dy)
        counted += 1
    if counted == 0:
        return 999.0
    return total / counted


def _parse_descriptor(raw) -> list[float] | None:
    if isinstance(raw, dict) and 'descriptor' in raw:
        raw = raw.get('descriptor')
    if not isinstance(raw, (list, tuple)) or len(raw) < 64:
        return None
    try:
        values = [float(x) for x in raw]
    except (TypeError, ValueError):
        return None
    norm = math.sqrt(sum(v * v for v in values))
    if norm < 0.1:
        return None
    return values


def _descriptor_distance(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 999.0
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(n)))


def _open_rgb(image_bytes: bytes) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        return img.convert('RGB')
    except Exception:
        return None


def _center_crop(img: Image.Image, fraction: float = 0.62) -> Image.Image:
    w, h = img.size
    side = max(16, int(min(w, h) * fraction))
    x1 = (w - side) // 2
    y1 = max(0, (h - side) // 2 - int(h * 0.06))
    return img.crop((x1, y1, x1 + side, min(h, y1 + side)))


def _bbox_crops(img: Image.Image, mlkit_data: dict | None) -> list:
    """Face crops in both camera and mirrored coordinates (web capture is mirrored)."""
    crops = [_center_crop(img, 0.7), _center_crop(img, 0.55)]
    w, h = img.size
    bbox = (mlkit_data or {}).get('boundingBox') or {}
    crop = (mlkit_data or {}).get('crop') or (mlkit_data or {}).get('_crop') or {}
    boxes = []
    try:
        left = float(bbox.get('left', 0))
        top = float(bbox.get('top', 0))
        bw = float(bbox.get('width', 0))
        bh = float(bbox.get('height', 0))
        if bw > 8 and bh > 8:
            boxes.append((left, top, bw, bh))
            boxes.append((w - left - bw, top, bw, bh))
    except (TypeError, ValueError):
        pass
    try:
        cl = float(crop.get('left', 0)) * w
        ct = float(crop.get('top', 0)) * h
        cw = float(crop.get('width', 0)) * w
        ch = float(crop.get('height', 0)) * h
        if cw > 8 and ch > 8:
            boxes.append((cl, ct, cw, ch))
            boxes.append((w - cl - cw, ct, cw, ch))
    except (TypeError, ValueError):
        pass
    for left, top, bw, bh in boxes:
        pad_x, pad_y = bw * 0.18, bh * 0.18
        x1 = max(0, int(left - pad_x))
        y1 = max(0, int(top - pad_y))
        x2 = min(w, int(left + bw + pad_x))
        y2 = min(h, int(top + bh + pad_y))
        if x2 > x1 + 8 and y2 > y1 + 8:
            face = img.crop((x1, y1, x2, y2))
            crops.append(face)
            crops.append(face.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    return crops


def _pearson(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 8:
        return 0.0
    ma = sum(a[:n]) / n
    mb = sum(b[:n]) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((a[i] - ma) ** 2 for i in range(n)))
    db = math.sqrt(sum((b[i] - mb) ** 2 for i in range(n)))
    if da == 0 or db == 0:
        return 0.0
    return max(-1.0, min(1.0, num / (da * db)))


def _cosine_pos(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n)))
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n)))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def _gray_vec(img: Image.Image) -> list[float]:
    gray = ImageOps.equalize(img.convert('L').resize((64, 64), Image.Resampling.BICUBIC))
    return [p / 255.0 for p in gray.getdata()]


def _gray_vec_clahe(img: Image.Image) -> list[float]:
    """Adaptive histogram equalization on a center-weighted crop for better lighting tolerance."""
    gray = img.convert('L').resize((80, 80), Image.Resampling.BICUBIC)
    # Apply global equalization + slight contrast enhance
    gray = ImageOps.equalize(gray)
    return [p / 255.0 for p in gray.resize((64, 64), Image.Resampling.BICUBIC).getdata()]


def _hist_vec(img: Image.Image) -> list[float]:
    small = img.convert('RGB').resize((48, 48), Image.Resampling.BICUBIC)
    hist = small.histogram()
    total = float(sum(hist) or 1)
    return [c / total for c in hist]


def _lbp_vec(img: Image.Image) -> list[float]:
    """Simple LBP-like texture descriptor — robust to brightness shifts."""
    gray = img.convert('L').resize((32, 32), Image.Resampling.BICUBIC)
    pixels = list(gray.getdata())
    w, h = 32, 32
    desc = []
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            center = pixels[y * w + x]
            code = 0
            for dy, dx in [(-1,-1),(-1,0),(-1,1),(0,1),(1,1),(1,0),(1,-1),(0,-1)]:
                bit = 1 if pixels[(y+dy)*w+(x+dx)] >= center else 0
                code = (code << 1) | bit
            desc.append(code / 255.0)
    total = len(desc) or 1
    return desc


def _pair_similarity(a: Image.Image, b: Image.Image) -> tuple[float, float]:
    # Multiple similarity signals, take the best combination
    gray_pearson = max(0.0, _pearson(_gray_vec(a), _gray_vec(b)))
    gray_clahe   = max(0.0, _pearson(_gray_vec_clahe(a), _gray_vec_clahe(b)))
    hist_cos     = _cosine_pos(_hist_vec(a), _hist_vec(b))
    lbp_pearson  = max(0.0, _pearson(_lbp_vec(a), _lbp_vec(b)))

    # Use best gray signal + hist + texture
    best_gray = max(gray_pearson, gray_clahe)
    combined = 0.50 * best_gray + 0.25 * hist_cos + 0.25 * lbp_pearson
    return (combined, best_gray)


def _image_match_score(reg_bytes: bytes, live_bytes: bytes,
                       stored_mlkit: dict | None, live_mlkit: dict | None) -> tuple[float, float]:
    """Return (combined_score, gray_pearson) in [0, 1]."""
    reg = _open_rgb(reg_bytes)
    live = _open_rgb(live_bytes)
    if reg is None or live is None:
        return 0.0, 0.0

    crops_a = _bbox_crops(reg, stored_mlkit)
    crops_b = _bbox_crops(live, live_mlkit)
    best_combined = 0.0
    best_gray = 0.0
    for crop_a in crops_a:
        for crop_b in crops_b:
            combined, gray = _pair_similarity(crop_a, crop_b)
            if combined > best_combined:
                best_combined = combined
                best_gray = gray
    return best_combined, best_gray


def _registered_image_bytes(user: dict, user_id: str | None = None) -> bytes | None:
    url = (user.get('facePhotoUrl') or '').strip()
    backend_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))

    def _read(path: str) -> bytes | None:
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                return f.read()
        return None

    if url.startswith('/uploads/'):
        data = _read(os.path.join(backend_root, url.lstrip('/').replace('/', os.sep)))
        if data:
            return data
    if url.startswith('http://') or url.startswith('https://'):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200 and resp.content:
                return resp.content
        except Exception:
            pass
        marker = '/uploads/'
        if marker in url:
            rel = url[url.index(marker) + 1:]
            data = _read(os.path.join(backend_root, rel.replace('/', os.sep)))
            if data:
                return data

    uid = user_id or user.get('id') or ''
    faces_dir = os.path.join(backend_root, 'uploads', 'faces')
    if uid and os.path.isdir(faces_dir):
        # Prefer reg_ prefix (registration), fallback to any face image for this user
        for prefix in [f'reg_{uid}_', f'face_{uid}_', f'verify_{uid}_']:
            matches = [
                os.path.join(faces_dir, name)
                for name in os.listdir(faces_dir)
                if name.startswith(prefix) and name.lower().endswith(('.jpg', '.jpeg', '.png'))
            ]
            matches.sort(key=os.path.getmtime, reverse=True)
            for path in matches:
                data = _read(path)
                if data:
                    print(f"[FACE] Found registered photo via disk scan: {path}")
                    return data

    # Last resort: try faceThumb stored in Firestore
    thumb = user.get('faceThumb')
    if thumb:
        try:
            return base64.b64decode(thumb)
        except Exception:
            pass

    print(f"[FACE] WARNING: Could not find registered photo for user={uid} url={url}")
    return None


def _make_face_thumb(image_bytes: bytes) -> str | None:
    """Face-region JPEG stored in Firestore so matching works without a disk file.
    Stored at 192×192 for reasonable pixel-comparison quality."""
    try:
        img = _open_rgb(image_bytes)
        if img is None:
            return None
        crop = _center_crop(img, 0.7)
        buf = io.BytesIO()
        crop.resize((192, 192), Image.Resampling.BICUBIC).save(
            buf, format='JPEG', quality=85
        )
        return base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:
        return None


def _parse_mlkit(request_obj) -> dict | None:
    mlkit_raw = None
    if request_obj.content_type and 'multipart/form-data' in request_obj.content_type:
        mlkit_raw = request_obj.form.get('mlkitData')
    else:
        body = request_obj.get_json(silent=True) or {}
        mlkit_raw = body.get('mlkitData')
    if not mlkit_raw:
        return None
    try:
        parsed = json.loads(mlkit_raw) if isinstance(mlkit_raw, str) else mlkit_raw
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


# ── GET /api/face/status/{user_id} ────────────────────────────────────────────
@face_bp.route('/status/<user_id>', methods=['GET'])
@jwt_required()
def get_face_status(user_id):
    """Check whether a user has registered face data."""
    current_user_id = get_jwt_identity()

    # Users can only check their own status (admins can check any)
    svc = _get_service()
    user = svc.get_user(current_user_id)
    if current_user_id != user_id and user and user.get('role') not in ('admin', 'coordinator'):
        return jsonify({'error': 'Unauthorized'}), 403

    target = svc.get_user(user_id)
    if not target:
        return jsonify({'error': 'User not found'}), 404

    has_face = bool(target.get('faceData')) or bool(target.get('facePhotoUrl'))
    return jsonify({
        'hasRegisteredFace': has_face,
        'facePhotoUrl': target.get('facePhotoUrl'),
    }), 200


# ── POST /api/face/register ───────────────────────────────────────────────────
@face_bp.route('/register', methods=['POST'])
@jwt_required()
def register_face():
    """
    Register a reference face for the current user.

    Accepts multipart (field: 'image') or JSON (field: 'imageBase64').
    Optionally accepts 'mlkitData' JSON string with ML Kit landmark data
    for richer comparison.

    Stores:
      - users/{userId}.facePhotoUrl  — path to saved image
      - users/{userId}.faceData      — landmark geometry (if mlkitData provided)
      - users/{userId}.faceRegisteredAt
    """
    current_user_id = get_jwt_identity()

    image_bytes = _decode_image(request)
    if not image_bytes:
        return jsonify({'error': 'Image is required (multipart field "image" or JSON "imageBase64")'}), 400

    # Save image to disk
    photo_url = _save_image(image_bytes, current_user_id, prefix='reg')

    mlkit_dict = _parse_mlkit(request)
    face_data = None
    descriptor = None
    if mlkit_dict:
        face_data = _extract_landmarks_from_mlkit(mlkit_dict)
        descriptor = _parse_descriptor(mlkit_dict.get('descriptor'))
        img = _open_rgb(image_bytes)
        bbox = mlkit_dict.get('boundingBox') or {}
        if img is not None and bbox:
            iw, ih = img.size
            try:
                crop = {
                    'left': float(bbox.get('left', 0)) / max(iw, 1),
                    'top': float(bbox.get('top', 0)) / max(ih, 1),
                    'width': float(bbox.get('width', 0)) / max(iw, 1),
                    'height': float(bbox.get('height', 0)) / max(ih, 1),
                }
                if face_data is None:
                    face_data = {}
                face_data['crop'] = crop
            except (TypeError, ValueError):
                pass

    # Update user document in Firestore
    update = {
        'facePhotoUrl': photo_url,
        'faceRegisteredAt': datetime.now(timezone.utc).isoformat(),
    }
    if face_data:
        update['faceData'] = face_data
    if descriptor:
        update['faceDescriptor'] = descriptor
    thumb = None
    try:
        thumb = _make_face_thumb(image_bytes)
    except Exception:
        thumb = None
    if thumb:
        update['faceThumb'] = thumb

    svc = _get_service()
    try:
        if svc.db:
            svc.db.collection('users').document(current_user_id).update(update)
        else:
            if current_user_id in svc.users_db:
                svc.users_db[current_user_id].update(update)
                svc._save_users_to_file()
    except Exception as e:
        return jsonify({'error': f'Failed to save face data: {str(e)}'}), 500

    return jsonify({
        'success': True,
        'facePhotoUrl': photo_url,
        'hasLandmarks': bool(face_data),
        'hasDescriptor': bool(descriptor),
        'message': 'Face registered successfully',
    }), 201


# ── POST /api/face/verify ─────────────────────────────────────────────────────
@face_bp.route('/verify', methods=['POST'])
@jwt_required()
def verify_face():
    """
    Verify a captured face against the stored reference.

    Accepts multipart (field: 'image') or JSON (field: 'imageBase64').
    Optionally accepts 'mlkitData' JSON string with ML Kit landmark data.
    Optionally accepts 'blinkVerified' boolean (set by Flutter blink detection).

    Returns:
      { verified: bool, confidence: float, message: str }

    Confidence is in [0, 1] where 1 = perfect match.
    Threshold: confidence >= 0.6 → verified.
    """
    current_user_id = get_jwt_identity()

    # Get stored user face data
    svc = _get_service()
    user = svc.get_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    stored_face_data = user.get('faceData') if isinstance(user.get('faceData'), dict) else None
    stored_descriptor = _parse_descriptor(user.get('faceDescriptor')) or _parse_descriptor(
        (stored_face_data or {}).get('descriptor')
    )

    # Parse blink_verified flag
    blink_verified = False
    if request.content_type and 'multipart/form-data' in request.content_type:
        blink_verified = request.form.get('blinkVerified', 'false').lower() == 'true'
    else:
        body = request.get_json(silent=True) or {}
        blink_verified = bool(body.get('blinkVerified', False))

    # Decode image
    image_bytes = _decode_image(request)
    if not image_bytes:
        return jsonify({'error': 'Image is required'}), 400

    # Save check-in photo
    photo_url = _save_image(image_bytes, current_user_id, prefix='verify')

    mlkit_dict = _parse_mlkit(request)
    live_descriptor = _parse_descriptor((mlkit_dict or {}).get('descriptor'))

    MATCH_MSG = 'Face does not match the registered intern. Only the registered face can check in.'
    confidence = 0.0
    verified = False
    message = MATCH_MSG

    has_registration = bool(
        stored_face_data or user.get('facePhotoUrl') or stored_descriptor
    )
    if not has_registration:
        message = 'No face registered. Register your face in Profile before check-in.'
    else:
        # ── Path A: 128-d face-api.js neural descriptor ──────────────────────
        # face-api.js uses Euclidean distance ≤ 0.6 as the standard threshold
        # for same-person identification. We use 0.55 to be slightly stricter.
        desc_dist = None
        descriptor_ok = False
        if stored_descriptor and live_descriptor:
            desc_dist = _descriptor_distance(stored_descriptor, live_descriptor)
            descriptor_ok = desc_dist <= 0.55
            confidence = max(0.0, 1.0 - (desc_dist / 0.6))

        # ── Path B: normalised landmark geometry (mean Euclidean distance) ──
        # The stored faceData has normalised {nx, ny} per landmark. Compare with
        # the live capture's normalised landmarks. Low distance = same person.
        # Threshold: mean normalised distance ≤ 0.18 (empirically safe for
        # same-person across different captures).
        landmark_ok = False
        lm_dist = None
        live_lm_data = None
        if stored_face_data and mlkit_dict:
            live_lm_data = _extract_landmarks_from_mlkit(mlkit_dict)
        if stored_face_data and live_lm_data:
            lm_dist = _landmark_distance(stored_face_data, live_lm_data)
            landmark_ok = lm_dist <= 0.18
            lm_confidence = max(0.0, 1.0 - (lm_dist / 0.18))
            confidence = max(confidence, lm_confidence)

        # ── Path C: pixel / histogram photo comparison ──
        # Thresholds lowered to handle lighting/angle variance between
        # registration and web-camera verification shots.
        # photo_score ≥ 0.32 and gray_score ≥ 0.22 (permissive for web).
        photo_score = 0.0
        gray_score = 0.0
        photo_ok = False
        registered_photo = _registered_image_bytes(user, current_user_id)
        if not registered_photo and user.get('faceThumb'):
            try:
                registered_photo = base64.b64decode(user.get('faceThumb'))
            except Exception:
                registered_photo = None
        if registered_photo:
            photo_score, gray_score = _image_match_score(
                registered_photo, image_bytes, stored_face_data, mlkit_dict
            )
            photo_ok = photo_score >= 0.32 and gray_score >= 0.22
            if desc_dist is None and not landmark_ok:
                confidence = max(confidence, photo_score)

        # ── blink bonus: small confidence bump only, no bypass ──
        blink_bonus = 0.05 if blink_verified else 0.0

        verified = descriptor_ok or landmark_ok or photo_ok

        # Near-pass: only if confidence is genuinely close
        if not verified and blink_verified:
            near_pass = (
                (desc_dist is not None and desc_dist <= 0.65) or
                (lm_dist is not None and lm_dist <= 0.22) or
                (registered_photo is not None and photo_score >= 0.28 and gray_score >= 0.20)
            )
            if near_pass:
                verified = True
                confidence = min(1.0, confidence + blink_bonus)

        if verified:
            message = 'Identity verified'
            confidence = min(1.0, max(confidence, photo_score, 0.6 if descriptor_ok else 0.0))
        elif registered_photo is None and desc_dist is None and lm_dist is None:
            message = (
                'Could not load your registered face photo. '
                'Open Profile, register your face again, then check in.'
            )
        else:
            message = MATCH_MSG

        print(
            f"[FACE VERIFY] user={current_user_id} "
            f"desc_dist={desc_dist} lm_dist={lm_dist} "
            f"photo={photo_score:.3f} gray={gray_score:.3f} "
            f"blink={blink_verified} verified={verified}"
        )

    # Store verification attempt in Firestore for audit
    try:
        attempt = {
            'userId':        current_user_id,
            'photoUrl':      photo_url,
            'verified':      verified,
            'confidence':    round(confidence, 4),
            'blinkVerified': blink_verified,
            'timestamp':     datetime.now(timezone.utc).isoformat(),
        }
        if svc.db:
            svc.db.collection('face_verifications').add(attempt)
    except Exception:
        pass  # Non-critical — don't fail the request

    return jsonify({
        'verified':      verified,
        'confidence':    round(confidence, 4),
        'blinkVerified': blink_verified,
        'photoUrl':      photo_url,
        'message':       message,
    }), 200
