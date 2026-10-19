"""
TrackIt System - Location Service
Handles reverse geocoding and location tracking
"""

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import time
import logging

logger = logging.getLogger(__name__)

class LocationService:
    """Service for reverse geocoding and location operations"""
    
    _geocoder = None
    
    @classmethod
    def _get_geocoder(cls):
        """Get or create Nominatim geocoder"""
        if cls._geocoder is None:
            cls._geocoder = Nominatim(user_agent="trackit_attendance_system")
        return cls._geocoder
    
    @classmethod
    def get_address_from_coordinates(cls, latitude, longitude, timeout=10):
        """
        Get address from GPS coordinates using reverse geocoding
        
        Args:
            latitude: GPS latitude
            longitude: GPS longitude
            timeout: Request timeout in seconds
            
        Returns:
            str: Formatted address or None if not found
        """
        if latitude is None or longitude is None:
            return None
            
        try:
            geocoder = cls._get_geocoder()
            location = geocoder.reverse(
                f"{latitude}, {longitude}",
                timeout=timeout,
                language='en'
            )
            
            if location and location.address:
                return cls._format_address(location.address)
                
        except GeocoderTimedOut:
            logger.warning(f"Geocoding timeout for {latitude}, {longitude}")
        except GeocoderServiceError as e:
            logger.error(f"Geocoding service error: {e}")
        except Exception as e:
            logger.error(f"Geocoding error: {e}")
            
        return None
    
    @classmethod
    def _format_address(cls, raw_address):
        """
        Format raw address into readable format
        
        Args:
            raw_address: Raw address from geocoder
            
        Returns:
            str: Formatted address
        """
        if not raw_address:
            return ""
            
        parts = raw_address.split(', ')
        if len(parts) >= 2:
            street = parts[0] if parts[0] else ''
            city = parts[1] if len(parts) > 1 else ''
            region = parts[2] if len(parts) > 2 else ''
            
            if street and city:
                return f"{street}, {city}"
            elif city:
                return city
                
        return raw_address
    
    @classmethod
    def get_location_details(cls, latitude, longitude):
        """
        Get detailed location info from coordinates
        
        Args:
            latitude: GPS latitude
            longitude: GPS longitude
            
        Returns:
            dict: Location details including address, city, region, country
        """
        if latitude is None or longitude is None:
            return None
            
        try:
            geocoder = cls._get_geocoder()
            location = geocoder.reverse(
                f"{latitude}, {longitude}",
                timeout=10,
                language='en'
            )
            
            if location:
                addr = location.raw.get('address', {})
                return {
                    'street': addr.get('road', '') or addr.get('street', ''),
                    'city': addr.get('city', '') or addr.get('municipality', ''),
                    'region': addr.get('state', ''),
                    'country': addr.get('country', ''),
                    'postal_code': addr.get('postcode', ''),
                    'full_address': location.address,
                    'latitude': latitude,
                    'longitude': longitude
                }
                
        except Exception as e:
            logger.error(f"Error getting location details: {e}")
            
        return {
            'latitude': latitude,
            'longitude': longitude,
            'full_address': f"{latitude}, {longitude}"
        }
    
    @classmethod
    def is_within_radius(cls, user_lat, user_lon, target_lat, target_lon, radius_meters=100):
        """
        Check if user location is within radius of target location
        
        Args:
            user_lat: User latitude
            user_lon: User longitude
            target_lat: Target latitude
            target_lon: Target longitude
            radius_meters: Radius in meters
            
        Returns:
            bool: True if within radius
        """
        from math import radians, cos, sin, asin, sqrt
        
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371000
            phi1, phi2 = radians(lat1), radians(lat2)
            dphi = radians(lat2 - lat1)
            dlambda = radians(lon2 - lon1)
            
            a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
            c = 2 * asin(sqrt(a))
            return R * c
        
        distance = haversine(user_lat, user_lon, target_lat, target_lon)
        return distance <= radius_meters