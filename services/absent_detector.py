"""
Absent Detector Service — runs scheduled checks at morning and afternoon cutoffs.
Uses APScheduler if available, otherwise falls back to a threading.Timer approach.
Timezone: Asia/Manila (UTC+8).
"""

import logging
import threading
from datetime import datetime, timedelta, timezone, date as date_type

logger = logging.getLogger(__name__)

# Default schedule times (Manila)
DEFAULT_MORNING_CHECKOUT  = '12:00'   # morning absent check fires at this time
DEFAULT_AFTERNOON_CUTOFF  = '18:00'   # afternoon absent check fires at this time
_MANILA_OFFSET = timedelta(hours=8)


def _manila_now():
    """Return current datetime in Asia/Manila (UTC+8) as a naive datetime."""
    return datetime.now(timezone.utc) + _MANILA_OFFSET


def _parse_hhmm(hhmm: str):
    """Return (hour, minute) tuple from 'HH:MM' string."""
    h, m = map(int, (hhmm or '00:00').split(':'))
    return h, m


def _seconds_until(target_h: int, target_m: int, ref: datetime) -> float:
    """Seconds from ref until next occurrence of HH:MM (same day or tomorrow)."""
    today_target = ref.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    diff = (today_target - ref).total_seconds()
    if diff < 0:
        diff += 86400  # schedule for tomorrow
    return diff


class AbsentDetectorService:
    """Detects and creates absent records for each OJT session cutoff."""

    def __init__(self, firebase_svc=None):
        self._firebase_svc = firebase_svc
        self._morning_ran_date: str = ''   # YYYY-MM-DD of last morning run
        self._afternoon_ran_date: str = '' # YYYY-MM-DD of last afternoon run
        self._scheduler = None
        self._timers: list = []

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Schedule the two daily absent-check jobs. Non-blocking."""
        logger.info('[AbsentDetector] Starting absent detector service')
        # Retrieve schedule times from global settings (best-effort)
        morning_checkout_hhmm = '12:00'
        afternoon_cutoff_hhmm = '18:00'
        try:
            if self._firebase_svc:
                s = self._firebase_svc.get_settings()
                morning_checkout_hhmm = s.get('ojtMorningCheckoutTime', '12:00')
        except Exception:
            pass

        tried_apscheduler = self._try_start_apscheduler(
            morning_checkout_hhmm, afternoon_cutoff_hhmm
        )
        if not tried_apscheduler:
            logger.warning('[AbsentDetector] APScheduler not available — using threading.Timer fallback')
            self._start_timer_fallback(morning_checkout_hhmm, afternoon_cutoff_hhmm)

        # Catch-up: run missed jobs immediately if we are past their fire time today
        now = _manila_now()
        today_str = now.strftime('%Y-%m-%d')
        mc_h, mc_m = _parse_hhmm(morning_checkout_hhmm)
        ac_h, ac_m = _parse_hhmm(afternoon_cutoff_hhmm)

        morning_cutoff_passed = now.hour * 60 + now.minute >= mc_h * 60 + mc_m
        afternoon_cutoff_passed = now.hour * 60 + now.minute >= ac_h * 60 + ac_m

        if morning_cutoff_passed and self._morning_ran_date != today_str:
            logger.info('[AbsentDetector] Past morning cutoff on startup — running morning check now')
            t = threading.Thread(target=self._safe_run_morning, daemon=True)
            t.start()

        if afternoon_cutoff_passed and self._afternoon_ran_date != today_str:
            logger.info('[AbsentDetector] Past afternoon cutoff on startup — running afternoon check now')
            t = threading.Thread(target=self._safe_run_afternoon, daemon=True)
            t.start()

    # ── APScheduler path ──────────────────────────────────────────────────────

    def _try_start_apscheduler(self, morning_hhmm: str, afternoon_hhmm: str) -> bool:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger

            mc_h, mc_m = _parse_hhmm(morning_hhmm)
            ac_h, ac_m = _parse_hhmm(afternoon_hhmm)

            # Try Asia/Manila tz; fall back to UTC+8 offset
            try:
                import pytz
                tz = pytz.timezone('Asia/Manila')
            except Exception:
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo('Asia/Manila')
                except Exception:
                    tz = timezone(timedelta(hours=8))

            scheduler = BackgroundScheduler(timezone=tz)
            scheduler.add_job(
                self._safe_run_morning,
                CronTrigger(hour=mc_h, minute=mc_m, timezone=tz),
                id='morning_absent_check',
                replace_existing=True,
                misfire_grace_time=3600,
            )
            scheduler.add_job(
                self._safe_run_afternoon,
                CronTrigger(hour=ac_h, minute=ac_m, timezone=tz),
                id='afternoon_absent_check',
                replace_existing=True,
                misfire_grace_time=3600,
            )
            scheduler.start()
            self._scheduler = scheduler
            logger.info(
                f'[AbsentDetector] APScheduler running: morning@{mc_h:02d}:{mc_m:02d}, afternoon@{ac_h:02d}:{ac_m:02d} (Manila)'
            )
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning(f'[AbsentDetector] APScheduler start failed: {e}')
            return False

    # ── threading.Timer fallback ──────────────────────────────────────────────

    def _start_timer_fallback(self, morning_hhmm: str, afternoon_hhmm: str):
        self._schedule_timer('morning', morning_hhmm)
        self._schedule_timer('afternoon', afternoon_hhmm)

    def _schedule_timer(self, session: str, hhmm: str):
        now = _manila_now()
        h, m = _parse_hhmm(hhmm)
        delay = _seconds_until(h, m, now)
        t = threading.Timer(delay, self._timer_fired, args=(session, hhmm))
        t.daemon = True
        t.start()
        self._timers.append(t)
        logger.info(f'[AbsentDetector] Timer scheduled for {session} in {delay:.0f}s')

    def _timer_fired(self, session: str, hhmm: str):
        if session == 'morning':
            self._safe_run_morning()
        else:
            self._safe_run_afternoon()
        # Reschedule for tomorrow
        self._schedule_timer(session, hhmm)

    # ── Job wrappers ──────────────────────────────────────────────────────────

    def _safe_run_morning(self):
        today_str = _manila_now().strftime('%Y-%m-%d')
        if self._morning_ran_date == today_str:
            logger.debug('[AbsentDetector] Morning check already ran today — skipping')
            return
        self._morning_ran_date = today_str
        try:
            self.check_morning_absent()
        except Exception as e:
            logger.error(f'[AbsentDetector] Morning absent check failed: {e}')

    def _safe_run_afternoon(self):
        today_str = _manila_now().strftime('%Y-%m-%d')
        if self._afternoon_ran_date == today_str:
            logger.debug('[AbsentDetector] Afternoon check already ran today — skipping')
            return
        self._afternoon_ran_date = today_str
        try:
            self.check_afternoon_absent()
        except Exception as e:
            logger.error(f'[AbsentDetector] Afternoon absent check failed: {e}')

    # ── Core detection logic ──────────────────────────────────────────────────

    def check_morning_absent(self):
        """Mark morning-session absent for every intern who has no morning check-in today."""
        svc = self._firebase_svc
        if not svc:
            logger.warning('[AbsentDetector] No firebase service — cannot run morning check')
            return
        today_str = _manila_now().strftime('%Y-%m-%d')
        logger.info(f'[AbsentDetector] Running morning absent check for {today_str}')
        students = self._get_all_interns(svc)
        created = 0
        for student in students:
            uid = student.get('id') or student.get('userId')
            if not uid:
                continue
            try:
                if not create_absent_record(uid, today_str, 'morning', svc):
                    continue
                created += 1
            except Exception as e:
                logger.warning(f'[AbsentDetector] morning: error for {uid}: {e}')
        logger.info(f'[AbsentDetector] Morning check complete — created {created} absent records')

    def check_afternoon_absent(self):
        """Mark afternoon-session absent for every intern who has no afternoon check-in today."""
        svc = self._firebase_svc
        if not svc:
            logger.warning('[AbsentDetector] No firebase service — cannot run afternoon check')
            return
        today_str = _manila_now().strftime('%Y-%m-%d')
        logger.info(f'[AbsentDetector] Running afternoon absent check for {today_str}')
        students = self._get_all_interns(svc)
        created = 0
        for student in students:
            uid = student.get('id') or student.get('userId')
            if not uid:
                continue
            try:
                if not create_absent_record(uid, today_str, 'afternoon', svc):
                    continue
                created += 1
            except Exception as e:
                logger.warning(f'[AbsentDetector] afternoon: error for {uid}: {e}')
        logger.info(f'[AbsentDetector] Afternoon check complete — created {created} absent records')

    @staticmethod
    def _get_all_interns(svc) -> list:
        try:
            return svc.get_all_students()
        except Exception as e:
            logger.warning(f'[AbsentDetector] get_all_students failed: {e}')
            return []


# ── Helper function ───────────────────────────────────────────────────────────

def create_absent_record(user_id: str, date_str: str, session: str, firebase_svc) -> bool:
    """Create an absent attendance record if no valid check-in exists for user/date/session.

    Returns True if a new absent record was created, False if skipped.
    The function is idempotent — safe to call multiple times.
    """
    try:
        existing = firebase_svc.get_today_attendance(user_id, date_str)
    except Exception as e:
        logger.warning(f'[create_absent_record] get_today_attendance failed for {user_id}/{date_str}: {e}')
        return False

    if existing:
        time_logs = existing.get('timeLogs') or []

        # If any timeLogs entry for this session has a check-in → do NOT mark absent
        has_checkin = any(
            e.get('session') == session and e.get('checkIn')
            for e in time_logs
        )
        if has_checkin:
            return False

        # If an auto-absent record already exists for this session → skip
        already_absent = any(
            (e.get('autoAbsent') is True and e.get('session') == session)
            or e.get('absentType') == session
            for e in time_logs
        )
        if already_absent:
            return False

        # If the top-level record is an absent for this exact session → skip
        if (
            existing.get('autoAbsent') is True
            and existing.get('session') == session
        ):
            return False

        # Append a new absent log entry to the existing record
        now_iso = datetime.now(timezone.utc).isoformat()
        absent_log = {
            'session':    session,
            'absentType': session,
            'autoAbsent': True,
            'status':     'absent',
            'createdAt':  now_iso,
        }
        time_logs.append(absent_log)
        try:
            firebase_svc.update_attendance_record(existing['id'], {
                'timeLogs':  time_logs,
                'updatedAt': now_iso,
            })
            logger.info(f'[create_absent_record] Appended {session} absent log to existing record for {user_id} on {date_str}')
            return True
        except Exception as e:
            logger.warning(f'[create_absent_record] update failed for {user_id}: {e}')
            return False

    # No record at all for today — create a fresh absent record
    now_iso = datetime.now(timezone.utc).isoformat()
    record = {
        'userId':     user_id,
        'date':       date_str,
        'status':     'absent',
        'session':    session,
        'absentType': session,
        'autoAbsent': True,
        'createdAt':  now_iso,
        'timeLogs': [{
            'session':    session,
            'absentType': session,
            'autoAbsent': True,
            'status':     'absent',
            'createdAt':  now_iso,
        }],
    }
    try:
        result = firebase_svc.create_attendance_record(record)
        if result.get('success'):
            logger.info(f'[create_absent_record] Created {session} absent record for {user_id} on {date_str}')
            return True
        logger.warning(f'[create_absent_record] create failed for {user_id}: {result.get("error")}')
        return False
    except Exception as e:
        logger.warning(f'[create_absent_record] create exception for {user_id}: {e}')
        return False
