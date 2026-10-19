"""
Bug Condition Exploration Test for Dashboard Total Interns Count

**Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2**

This test MUST FAIL on unfixed code - failure confirms the bug exists.
DO NOT attempt to fix the test or the code when it fails.

Property 1: Bug Condition - Dashboard Returns Incorrect Total Student Count

The test encodes the expected behavior - it will validate the fix when it passes after implementation.
GOAL: Surface counterexamples that demonstrate the bug exists.

Expected Counterexample:
- totalEmployees returns 2 (from employees collection) instead of 23 (actual students)
"""

import unittest
import sys
import os
from unittest.mock import Mock, MagicMock

# Add parent directory to path to import firebase_service
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.firebase_service import FirebaseService


class TestDashboardBugCondition(unittest.TestCase):
    """
    Bug Condition Exploration Test
    
    Tests the concrete failing case: with 23 students in users collection 
    and 2 employees in employees collection, verify that totalEmployees 
    should equal 23.
    
    **CRITICAL**: This test MUST FAIL on unfixed code
    """
    
    def setUp(self):
        """Set up test fixtures"""
        self.firebase_service = FirebaseService()
        
        # Mock the Firestore database
        self.firebase_service.db = MagicMock()
        
        # Create mock for employees collection (current buggy behavior)
        # The bug: queries employees collection which has 2 demo employees
        mock_employees_collection = MagicMock()
        mock_employee_docs = [MagicMock(), MagicMock()]  # 2 employees
        mock_employees_collection.get.return_value = mock_employee_docs
        
        # Create mock for users collection (correct behavior)
        # Should query users with role='student' which has 23 students
        mock_users_collection = MagicMock()
        mock_student_docs = [MagicMock() for _ in range(23)]  # 23 students
        mock_users_query = MagicMock()
        mock_users_query.get.return_value = mock_student_docs
        mock_users_collection.where.return_value = mock_users_query
        
        # Create mock for attendance collection (for other metrics)
        mock_attendance_collection = MagicMock()
        mock_attendance_collection.where.return_value.get.return_value = []
        
        # Create mock for reports collection (for pending reports)
        mock_reports_collection = MagicMock()
        mock_reports_collection.where.return_value.get.return_value = []
        
        # Set up collection routing
        def mock_collection(name):
            if name == 'employees':
                return mock_employees_collection
            elif name == 'users':
                return mock_users_collection
            elif name == 'attendance':
                return mock_attendance_collection
            elif name == 'reports':
                return mock_reports_collection
            return MagicMock()
        
        self.firebase_service.db.collection = mock_collection
    
    def test_bug_condition_total_employees_count(self):
        """
        Test that get_dashboard_stats() returns correct totalEmployees count
        
        **Validates: Requirements 2.1, 2.2**
        
        Expected Behavior (from design):
        - result.totalEmployees == COUNT(users WHERE role='student')
        
        Concrete failing case:
        - 23 students in users collection (role='student')
        - 2 employees in employees collection
        - Expected: totalEmployees = 23
        - Current (buggy): totalEmployees = 2
        
        **EXPECTED OUTCOME**: Test FAILS on unfixed code (proves bug exists)
        """
        # Act: Call the dashboard stats method
        result = self.firebase_service.get_dashboard_stats()
        
        # Assert: totalEmployees should equal actual student count (23), not employees count (2)
        actual_student_count = 23
        
        # This assertion will FAIL on unfixed code because:
        # - Unfixed code queries 'employees' collection (returns 2)
        # - Fixed code should query 'users' collection with role='student' (returns 23)
        self.assertEqual(
            result['totalEmployees'], 
            actual_student_count,
            f"Bug detected: totalEmployees returns {result['totalEmployees']} "
            f"(from employees collection) instead of {actual_student_count} "
            f"(actual students in users collection)"
        )
    
    def test_bug_condition_queries_wrong_collection(self):
        """
        Test that get_dashboard_stats() queries the correct collection
        
        **Validates: Requirements 1.1, 1.2, 1.3**
        
        This test verifies the root cause:
        - Current (buggy): queries 'employees' collection
        - Expected (fixed): queries 'users' collection with role='student' filter
        
        **EXPECTED OUTCOME**: Test FAILS on unfixed code (confirms root cause)
        """
        # Act: Call the dashboard stats method
        result = self.firebase_service.get_dashboard_stats()
        
        # Assert: Should query users collection, not employees collection
        # On unfixed code, this will fail because it queries employees
        users_collection = self.firebase_service.db.collection('users')
        
        # Verify that the users collection was queried with role='student' filter
        # This will FAIL on unfixed code because it doesn't query users at all
        users_collection.where.assert_called_with('role', '==', 'student')
    
    def test_bug_condition_absent_calculation_uses_wrong_total(self):
        """
        Test that absentToday calculation uses correct total
        
        **Validates: Requirements 1.2, 2.2**
        
        The bug affects the absent count calculation:
        - absentToday = total_employees - present_today - late_today
        - Current (buggy): uses 2 (employees) as base
        - Expected (fixed): uses 23 (students) as base
        
        With 0 present and 0 late:
        - Current (buggy): absentToday = 2 - 0 - 0 = 2
        - Expected (fixed): absentToday = 23 - 0 - 0 = 23
        
        **EXPECTED OUTCOME**: Test FAILS on unfixed code
        """
        # Act: Call the dashboard stats method (with no attendance today)
        result = self.firebase_service.get_dashboard_stats()
        
        # Assert: absentToday should be 23 (all students absent), not 2
        expected_absent = 23  # All 23 students are absent (0 present, 0 late)
        
        # This will FAIL on unfixed code because:
        # - Unfixed: absentToday = 2 - 0 - 0 = 2
        # - Fixed: absentToday = 23 - 0 - 0 = 23
        self.assertEqual(
            result['absentToday'],
            expected_absent,
            f"Bug detected: absentToday = {result['absentToday']} "
            f"(calculated from wrong total of 2) instead of {expected_absent} "
            f"(should be calculated from actual student count of 23)"
        )


if __name__ == '__main__':
    # Run the tests
    unittest.main(verbosity=2)
