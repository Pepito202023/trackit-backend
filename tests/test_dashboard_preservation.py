"""
Preservation Property Tests for Dashboard Metrics

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

Property 2: Preservation - Other Dashboard Metrics Remain Unchanged

These tests verify that non-buggy dashboard metrics (presentToday, lateArrivals, 
pendingReports, recentAttendance) remain unchanged by the fix.

**IMPORTANT**: Follow observation-first methodology
- Observe behavior on UNFIXED code for non-buggy inputs
- Write property-based tests capturing observed behavior patterns
- Run tests on UNFIXED code
- **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
"""

import unittest
import sys
import os
from unittest.mock import MagicMock
from datetime import datetime
from hypothesis import given, strategies as st, settings

# Add parent directory to path to import firebase_service
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.firebase_service import FirebaseService


class TestDashboardPreservation(unittest.TestCase):
    """
    Preservation Property Tests
    
    Tests that other dashboard metrics remain unchanged:
    - presentToday: counts today's attendance records with status='present'
    - lateArrivals: counts today's attendance records with status='late'
    - pendingReports: counts reports with status='pending'
    - recentAttendance: returns array of attendance records with details
    
    **EXPECTED OUTCOME**: All tests PASS on unfixed code (baseline behavior)
    """
    
    def create_firebase_service_with_data(self, present_count, late_count, pending_reports_count, recent_attendance_data):
        """Helper to create FirebaseService with mocked data"""
        firebase_service = FirebaseService()
        firebase_service.db = MagicMock()
        
        # Mock employees collection (buggy behavior - returns 2)
        mock_employees_collection = MagicMock()
        mock_employee_docs = [MagicMock(), MagicMock()]
        mock_employees_collection.get.return_value = mock_employee_docs
        
        # Mock attendance collection
        today = datetime.now().strftime('%Y-%m-%d')
        mock_attendance_collection = MagicMock()
        
        # Create attendance documents based on present and late counts
        attendance_docs = []
        for i in range(present_count):
            doc = MagicMock()
            doc.to_dict.return_value = {
                'status': 'present',
                'employeeName': f'Student {i}',
                'checkIn': '08:00 AM',
                'checkOut': '05:00 PM',
                'checkInLocation': {'address': f'Location {i}'},
                'date': today
            }
            attendance_docs.append(doc)
        
        for i in range(late_count):
            doc = MagicMock()
            doc.to_dict.return_value = {
                'status': 'late',
                'employeeName': f'Student {i + present_count}',
                'checkIn': '09:30 AM',
                'checkOut': '',
                'checkInLocation': {'address': f'Location {i + present_count}'},
                'date': today
            }
            attendance_docs.append(doc)
        
        mock_attendance_query = MagicMock()
        mock_attendance_query.get.return_value = attendance_docs
        mock_attendance_collection.where.return_value = mock_attendance_query
        
        # Mock reports collection
        mock_reports_collection = MagicMock()
        mock_report_docs = [MagicMock() for _ in range(pending_reports_count)]
        mock_reports_query = MagicMock()
        mock_reports_query.get.return_value = mock_report_docs
        mock_reports_collection.where.return_value = mock_reports_query
        
        # Set up collection routing
        def mock_collection(name):
            if name == 'employees':
                return mock_employees_collection
            elif name == 'attendance':
                return mock_attendance_collection
            elif name == 'reports':
                return mock_reports_collection
            return MagicMock()
        
        firebase_service.db.collection = mock_collection
        
        return firebase_service
    
    @given(
        present_count=st.integers(min_value=0, max_value=50),
        late_count=st.integers(min_value=0, max_value=50)
    )
    @settings(max_examples=50, deadline=None)
    def test_preservation_present_today_calculation(self, present_count, late_count):
        """
        Property: presentToday calculation remains unchanged
        
        **Validates: Requirements 3.1**
        
        Observed behavior on UNFIXED code:
        - presentToday counts today's attendance records with status='present'
        
        Property: For any number of present and late attendance records,
        presentToday should equal the count of records with status='present'
        
        **EXPECTED OUTCOME**: Test PASSES on unfixed code
        """
        # Arrange: Create service with random attendance data
        firebase_service = self.create_firebase_service_with_data(
            present_count=present_count,
            late_count=late_count,
            pending_reports_count=0,
            recent_attendance_data=[]
        )
        
        # Act: Get dashboard stats
        result = firebase_service.get_dashboard_stats()
        
        # Assert: presentToday should equal the count of present records
        self.assertEqual(
            result['presentToday'],
            present_count,
            f"presentToday calculation changed: expected {present_count}, got {result['presentToday']}"
        )
    
    @given(
        present_count=st.integers(min_value=0, max_value=50),
        late_count=st.integers(min_value=0, max_value=50)
    )
    @settings(max_examples=50, deadline=None)
    def test_preservation_late_arrivals_calculation(self, present_count, late_count):
        """
        Property: lateArrivals calculation remains unchanged
        
        **Validates: Requirements 3.3**
        
        Observed behavior on UNFIXED code:
        - lateArrivals counts today's attendance records with status='late'
        
        Property: For any number of present and late attendance records,
        lateArrivals should equal the count of records with status='late'
        
        **EXPECTED OUTCOME**: Test PASSES on unfixed code
        """
        # Arrange: Create service with random attendance data
        firebase_service = self.create_firebase_service_with_data(
            present_count=present_count,
            late_count=late_count,
            pending_reports_count=0,
            recent_attendance_data=[]
        )
        
        # Act: Get dashboard stats
        result = firebase_service.get_dashboard_stats()
        
        # Assert: lateArrivals should equal the count of late records
        self.assertEqual(
            result['lateArrivals'],
            late_count,
            f"lateArrivals calculation changed: expected {late_count}, got {result['lateArrivals']}"
        )
    
    @given(pending_reports_count=st.integers(min_value=0, max_value=100))
    @settings(max_examples=50, deadline=None)
    def test_preservation_pending_reports_calculation(self, pending_reports_count):
        """
        Property: pendingReports calculation remains unchanged
        
        **Validates: Requirements 3.5**
        
        Observed behavior on UNFIXED code:
        - pendingReports counts reports with status='pending'
        
        Property: For any number of pending reports,
        pendingReports should equal the count of reports with status='pending'
        
        **EXPECTED OUTCOME**: Test PASSES on unfixed code
        """
        # Arrange: Create service with random reports data
        firebase_service = self.create_firebase_service_with_data(
            present_count=0,
            late_count=0,
            pending_reports_count=pending_reports_count,
            recent_attendance_data=[]
        )
        
        # Act: Get dashboard stats
        result = firebase_service.get_dashboard_stats()
        
        # Assert: pendingReports should equal the count of pending reports
        self.assertEqual(
            result['pendingReports'],
            pending_reports_count,
            f"pendingReports calculation changed: expected {pending_reports_count}, got {result['pendingReports']}"
        )
    
    @given(
        present_count=st.integers(min_value=1, max_value=10),
        late_count=st.integers(min_value=0, max_value=10)
    )
    @settings(max_examples=30, deadline=None)
    def test_preservation_recent_attendance_structure(self, present_count, late_count):
        """
        Property: recentAttendance structure and content remain unchanged
        
        **Validates: Requirements 3.4**
        
        Observed behavior on UNFIXED code:
        - recentAttendance returns array of attendance records
        - Each record includes: employeeName, checkIn, checkOut, location, status
        - Returns first 5 records from today's attendance
        
        Property: For any attendance data, recentAttendance should:
        1. Be a list
        2. Contain at most 5 records
        3. Each record has required fields: employeeName, checkIn, checkOut, location, status
        
        **EXPECTED OUTCOME**: Test PASSES on unfixed code
        """
        # Arrange: Create service with random attendance data
        firebase_service = self.create_firebase_service_with_data(
            present_count=present_count,
            late_count=late_count,
            pending_reports_count=0,
            recent_attendance_data=[]
        )
        
        # Act: Get dashboard stats
        result = firebase_service.get_dashboard_stats()
        
        # Assert: recentAttendance structure is preserved
        recent_attendance = result['recentAttendance']
        
        # Should be a list
        self.assertIsInstance(
            recent_attendance,
            list,
            "recentAttendance should be a list"
        )
        
        # Should contain at most 5 records
        self.assertLessEqual(
            len(recent_attendance),
            5,
            f"recentAttendance should contain at most 5 records, got {len(recent_attendance)}"
        )
        
        # Each record should have required fields
        required_fields = ['employeeName', 'checkIn', 'checkOut', 'location', 'status']
        for record in recent_attendance:
            for field in required_fields:
                self.assertIn(
                    field,
                    record,
                    f"recentAttendance record missing required field: {field}"
                )
    
    @given(
        present_count=st.integers(min_value=0, max_value=20),
        late_count=st.integers(min_value=0, max_value=20)
    )
    @settings(max_examples=50, deadline=None)
    def test_preservation_absent_today_formula(self, present_count, late_count):
        """
        Property: absentToday calculation formula remains unchanged
        
        **Validates: Requirements 3.2**
        
        Observed behavior on UNFIXED code:
        - absentToday uses formula: total_employees - present_today - late_today
        - Note: The total_employees value is buggy (2 instead of 23), but the FORMULA is correct
        
        Property: For any attendance data, absentToday should equal:
        total_employees - presentToday - lateArrivals
        
        This test verifies the formula structure is preserved, not the correctness of total_employees
        
        **EXPECTED OUTCOME**: Test PASSES on unfixed code
        """
        # Arrange: Create service with random attendance data
        firebase_service = self.create_firebase_service_with_data(
            present_count=present_count,
            late_count=late_count,
            pending_reports_count=0,
            recent_attendance_data=[]
        )
        
        # Act: Get dashboard stats
        result = firebase_service.get_dashboard_stats()
        
        # Assert: absentToday formula is preserved
        # Formula: absentToday = totalEmployees - presentToday - lateArrivals
        expected_absent = result['totalEmployees'] - result['presentToday'] - result['lateArrivals']
        
        self.assertEqual(
            result['absentToday'],
            expected_absent,
            f"absentToday formula changed: expected {expected_absent} "
            f"(totalEmployees={result['totalEmployees']} - presentToday={result['presentToday']} - lateArrivals={result['lateArrivals']}), "
            f"got {result['absentToday']}"
        )
    
    def test_preservation_all_metrics_together(self):
        """
        Integration test: All preservation metrics work together
        
        **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
        
        Test a concrete scenario with specific values to ensure all metrics
        are calculated correctly together on unfixed code.
        
        **EXPECTED OUTCOME**: Test PASSES on unfixed code
        """
        # Arrange: Create service with specific test data
        present_count = 5
        late_count = 3
        pending_reports_count = 7
        
        firebase_service = self.create_firebase_service_with_data(
            present_count=present_count,
            late_count=late_count,
            pending_reports_count=pending_reports_count,
            recent_attendance_data=[]
        )
        
        # Act: Get dashboard stats
        result = firebase_service.get_dashboard_stats()
        
        # Assert: All metrics are calculated correctly
        self.assertEqual(result['presentToday'], present_count, "presentToday incorrect")
        self.assertEqual(result['lateArrivals'], late_count, "lateArrivals incorrect")
        self.assertEqual(result['pendingReports'], pending_reports_count, "pendingReports incorrect")
        
        # Verify absentToday formula
        expected_absent = result['totalEmployees'] - present_count - late_count
        self.assertEqual(result['absentToday'], expected_absent, "absentToday formula incorrect")
        
        # Verify recentAttendance structure
        self.assertIsInstance(result['recentAttendance'], list, "recentAttendance should be a list")
        self.assertLessEqual(len(result['recentAttendance']), 5, "recentAttendance should have at most 5 records")


if __name__ == '__main__':
    # Run the tests
    unittest.main(verbosity=2)
