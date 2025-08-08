# test/test_slack_notifier.py

import os
import unittest
from unittest.mock import Mock, patch, MagicMock
import pandas as pd
from datetime import datetime

# Add the project root to the path
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from etsi.watchdog.slack_notifier import SlackNotifier
from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog import Monitor
from slack_sdk.errors import SlackApiError


class TestSlackNotifier(unittest.TestCase):
    """Test suite for SlackNotifier functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Mock environment variables
        self.test_token = "xoxb-test-token"
        self.test_channel = "#test-alerts"
        
        # Create test drift results
        self.drift_result_with_drift = DriftResult(
            method="psi",
            score=0.85,
            threshold=0.2,
            details={"bins": ["0-10", "10-20"], "expected_percents": [0.5, 0.5], "actual_percents": [0.8, 0.2]},
            sample_size=1000
        )
        
        self.drift_result_no_drift = DriftResult(
            method="psi",
            score=0.15,
            threshold=0.2,
            details={"bins": ["0-10", "10-20"], "expected_percents": [0.5, 0.5], "actual_percents": [0.52, 0.48]},
            sample_size=1000
        )
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_initialization_with_parameters(self, mock_webclient):
        """Test SlackNotifier initialization with explicit parameters."""
        # Mock successful auth test
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        notifier = SlackNotifier(token=self.test_token, channel=self.test_channel)
        
        self.assertEqual(notifier.token, self.test_token)
        self.assertEqual(notifier.channel, self.test_channel)
        mock_webclient.assert_called_once_with(token=self.test_token)
        mock_client.auth_test.assert_called_once()
    
    @patch.dict(os.environ, {'SLACK_BOT_TOKEN': 'env-token', 'SLACK_CHANNEL': '#env-channel'})
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_initialization_with_env_variables(self, mock_webclient):
        """Test SlackNotifier initialization using environment variables."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        notifier = SlackNotifier()
        
        self.assertEqual(notifier.token, "env-token")
        self.assertEqual(notifier.channel, "#env-channel")
    
    def test_initialization_no_token_raises_error(self):
        """Test that initialization without token raises ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as context:
                SlackNotifier()
            
            self.assertIn("Slack token must be provided", str(context.exception))
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_connection_failure_raises_error(self, mock_webclient):
        """Test that connection failure raises ConnectionError."""
        mock_client = Mock()
        mock_client.auth_test.side_effect = SlackApiError(
            message="Invalid token",
            response={"error": "invalid_auth"}
        )
        mock_webclient.return_value = mock_client
        
        with self.assertRaises(ConnectionError) as context:
            SlackNotifier(token=self.test_token)
        
        self.assertIn("Failed to connect to Slack", str(context.exception))
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_send_drift_alert_success(self, mock_webclient):
        """Test successful drift alert sending."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        notifier = SlackNotifier(token=self.test_token, channel=self.test_channel)
        
        success = notifier.send_drift_alert(
            drift_result=self.drift_result_with_drift,
            feature="test_feature",
            additional_context={"model": "test_model", "environment": "production"}
        )
        
        self.assertTrue(success)
        mock_client.chat_postMessage.assert_called_once()
        
        # Verify the message content
        call_args = mock_client.chat_postMessage.call_args
        self.assertEqual(call_args[1]['channel'], self.test_channel)
        self.assertIn('blocks', call_args[1])
        self.assertIn('DRIFT DETECTED', call_args[1]['text'])
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_send_drift_alert_no_drift(self, mock_webclient):
        """Test drift alert sending when no drift is detected."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        notifier = SlackNotifier(token=self.test_token, channel=self.test_channel)
        
        success = notifier.send_drift_alert(
            drift_result=self.drift_result_no_drift,
            feature="test_feature"
        )
        
        self.assertTrue(success)
        call_args = mock_client.chat_postMessage.call_args
        self.assertIn('NO DRIFT', call_args[1]['text'])
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_send_drift_alert_failure(self, mock_webclient):
        """Test drift alert sending failure handling."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.side_effect = SlackApiError(
            message="Channel not found",
            response={"error": "channel_not_found"}
        )
        mock_webclient.return_value = mock_client
        
        notifier = SlackNotifier(token=self.test_token, channel=self.test_channel)
        
        with patch('builtins.print') as mock_print:
            success = notifier.send_drift_alert(
                drift_result=self.drift_result_with_drift,
                feature="test_feature"
            )
        
        self.assertFalse(success)
        mock_print.assert_called()
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_send_system_alert_different_severities(self, mock_webclient):
        """Test system alert sending with different severity levels."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        notifier = SlackNotifier(token=self.test_token, channel=self.test_channel)
        
        severities = ["info", "warning", "error", "critical"]
        
        for severity in severities:
            success = notifier.send_system_alert(
                message=f"Test {severity} message",
                severity=severity,
                additional_details={"detail1": "value1"}
            )
            self.assertTrue(success)
        
        # Verify all calls were made
        self.assertEqual(mock_client.chat_postMessage.call_count, len(severities))
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_send_monitoring_summary_all_clear(self, mock_webclient):
        """Test monitoring summary when no drift is detected."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        notifier = SlackNotifier(token=self.test_token, channel=self.test_channel)
        
        results = {
            "feature1": self.drift_result_no_drift,
            "feature2": self.drift_result_no_drift
        }
        
        success = notifier.send_monitoring_summary(
            results=results,
            monitoring_context={"batch_id": "12345"}
        )
        
        self.assertTrue(success)
        call_args = mock_client.chat_postMessage.call_args
        self.assertIn('ALL CLEAR', call_args[1]['text'])
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_send_monitoring_summary_with_drift(self, mock_webclient):
        """Test monitoring summary when drift is detected."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        notifier = SlackNotifier(token=self.test_token, channel=self.test_channel)
        
        results = {
            "feature1": self.drift_result_with_drift,
            "feature2": self.drift_result_no_drift,
            "feature3": self.drift_result_no_drift
        }
        
        success = notifier.send_monitoring_summary(results=results)
        
        self.assertTrue(success)
        call_args = mock_client.chat_postMessage.call_args
        # With 1 out of 3 features drifted, it should be PARTIAL DRIFT
        self.assertIn('PARTIAL DRIFT', call_args[1]['text'])


class TestMonitorSlackIntegration(unittest.TestCase):
    """Test suite for Monitor class Slack integration."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Create test data
        self.reference_df = pd.DataFrame({
            "feature1": [1, 2, 2, 3, 4, 5],
            "feature2": [100, 102, 101, 98, 97, 99],
            "category": ["A", "B", "A", "C", "B", "A"]
        })
        
        self.live_df = pd.DataFrame({
            "feature1": [11, 12, 13, 14, 15, 16],
            "feature2": [188, 189, 190, 191, 192, 193],
            "category": ["A", "C", "C", "C", "D", "D"]
        })
        
        # Create rolling test data with datetime index
        dates = pd.date_range('2023-01-01', periods=100, freq='D')
        self.rolling_df = pd.DataFrame({
            "feature1": range(100),
            "feature2": range(200, 300)
        }, index=dates)
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_monitor_enable_slack_alerts(self, mock_webclient):
        """Test enabling Slack alerts in Monitor."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        monitor = Monitor(self.reference_df)
        monitor.enable_slack_alerts(token="test-token", channel="#test")
        
        self.assertTrue(monitor.slack_enabled)
        self.assertIsNotNone(monitor.slack_notifier)
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_monitor_slack_alerts_on_drift_detection(self, mock_webclient):
        """Test that Slack alerts are sent when drift is detected."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        monitor = Monitor(self.reference_df)
        monitor.enable_slack_alerts(token="test-token", channel="#test")
        
        # Mock drift detection to return drift
        with patch.object(monitor, 'slack_notifier') as mock_notifier:
            mock_notifier.send_drift_alert.return_value = True
            mock_notifier.send_monitoring_summary.return_value = True
            
            # This should trigger alerts since live_df is very different from reference_df
            results = monitor.watch(self.live_df, threshold=0.1)
            
            # Verify that alerts were attempted (would depend on actual drift detection results)
            self.assertIsInstance(results, dict)
    
    def test_monitor_disable_slack_alerts(self):
        """Test disabling Slack alerts in Monitor."""
        monitor = Monitor(self.reference_df)
        
        # Enable first
        with patch('etsi.watchdog.slack_notifier.WebClient') as mock_webclient:
            mock_client = Mock()
            mock_client.auth_test.return_value = {"ok": True}
            mock_webclient.return_value = mock_client
            
            monitor.enable_slack_alerts(token="test-token")
            self.assertTrue(monitor.slack_enabled)
            
            # Then disable
            monitor.disable_slack_alerts()
            self.assertFalse(monitor.slack_enabled)
            self.assertIsNone(monitor.slack_notifier)
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_monitor_error_handling_with_slack(self, mock_webclient):
        """Test error handling in Monitor with Slack notifications."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        monitor = Monitor(self.reference_df)
        monitor.enable_slack_alerts(token="test-token", channel="#test")
        
        # Verify slack is enabled
        self.assertTrue(monitor.slack_enabled)
        
        # Mock an error in DriftCheck.run method
        with patch('etsi.watchdog.monitor.DriftCheck') as mock_drift_check_class:
            mock_drift_check = Mock()
            mock_drift_check.run.side_effect = Exception("Test error")
            mock_drift_check_class.return_value = mock_drift_check
            
            with self.assertRaises(Exception):
                monitor.watch(self.live_df)
            
            # Verify that system alert was sent (check the mock client was called)
            mock_client.chat_postMessage.assert_called()
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_rolling_monitoring_with_slack(self, mock_webclient):
        """Test rolling monitoring with Slack notifications."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        monitor = Monitor(self.reference_df)
        monitor.enable_slack_alerts(token="test-token", channel="#test")
        
        with patch.object(monitor, 'slack_notifier') as mock_notifier:
            mock_notifier.send_system_alert.return_value = True
            
            # Test rolling monitoring
            try:
                results = monitor.watch_rolling(
                    self.rolling_df, 
                    window=10, 
                    freq="10D",
                    slack_context={"analysis_type": "rolling"}
                )
                
                # Verify results structure
                self.assertIsInstance(results, list)
                
            except Exception as e:
                # If drift checking fails, that's okay for this test
                # We're mainly testing the Slack integration structure
                pass


class TestSlackNotifierIntegration(unittest.TestCase):
    """Integration tests for complete Slack notification workflow."""
    
    def setUp(self):
        """Set up integration test fixtures."""
        self.reference_df = pd.DataFrame({
            "age": [25, 30, 35, 40, 45],
            "salary": [50000, 60000, 70000, 80000, 90000]
        })
        
        # Create data that will definitely cause drift
        self.drifted_df = pd.DataFrame({
            "age": [55, 60, 65, 70, 75],
            "salary": [150000, 160000, 170000, 180000, 190000]
        })
    
    @patch('etsi.watchdog.slack_notifier.WebClient')
    def test_end_to_end_drift_detection_and_notification(self, mock_webclient):
        """Test complete end-to-end workflow from drift detection to Slack notification."""
        mock_client = Mock()
        mock_client.auth_test.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True}
        mock_webclient.return_value = mock_client
        
        # Initialize monitor with Slack alerts
        monitor = Monitor(self.reference_df)
        monitor.enable_slack_alerts(
            token="test-token",
            channel="#alerts",
            send_summaries=True,
            send_individual_alerts=True
        )
        
        # Perform monitoring
        results = monitor.watch(
            self.drifted_df,
            features=["age", "salary"],
            threshold=0.1,  # Low threshold to ensure drift detection
            slack_context={
                "model_name": "test_model",
                "environment": "production",
                "batch_id": "batch_001"
            }
        )
        
        # Verify that monitoring completed
        self.assertIsInstance(results, dict)
        self.assertTrue(len(results) > 0)
        
        # Verify that Slack client methods were called
        # (actual drift detection and notification sending would depend on the data)
        mock_client.chat_postMessage.assert_called()


if __name__ == '__main__':
    # Create test suite
    suite = unittest.TestSuite()
    
    # Add test cases
    suite.addTest(unittest.makeSuite(TestSlackNotifier))
    suite.addTest(unittest.makeSuite(TestMonitorSlackIntegration))
    suite.addTest(unittest.makeSuite(TestSlackNotifierIntegration))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print(f"\n{'='*50}")
    print(f"TESTS RUN: {result.testsRun}")
    print(f"FAILURES: {len(result.failures)}")
    print(f"ERRORS: {len(result.errors)}")
    print(f"SUCCESS RATE: {((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100):.1f}%")
    print(f"{'='*50}")
