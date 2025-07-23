# etsi/watchdog/slack_notifier.py

import os
import json
from datetime import datetime
from typing import Optional, Dict, Any
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .drift.base import DriftResult


class SlackNotifier:
    """
    SlackNotifier — Send drift detection alerts to Slack channels.
    
    This class handles sending notifications to Slack when drift is detected
    or critical events occur in the watchdog monitoring system.
    
    Environment Variables:
    - SLACK_BOT_TOKEN: The Slack bot token for authentication
    - SLACK_CHANNEL: Default channel to send notifications (can be overridden)
    
    Example:
    >>> notifier = SlackNotifier()
    >>> notifier.send_drift_alert(drift_result, feature="age")
    """
    
    def __init__(self, token: Optional[str] = None, channel: Optional[str] = None):
        """
        Initialize the Slack notifier.
        
        Args:
            token: Slack bot token (if not provided, uses SLACK_BOT_TOKEN env var)
            channel: Default channel to send messages (if not provided, uses SLACK_CHANNEL env var)
        """
        self.token = token or os.getenv("SLACK_BOT_TOKEN")
        self.channel = channel or os.getenv("SLACK_CHANNEL", "#alerts")
        
        if not self.token:
            raise ValueError(
                "Slack token must be provided either as parameter or through SLACK_BOT_TOKEN environment variable"
            )
        
        self.client = WebClient(token=self.token)
        self._test_connection()
    
    def _test_connection(self) -> bool:
        """Test the Slack connection and permissions."""
        try:
            response = self.client.auth_test()
            return response["ok"]
        except SlackApiError as e:
            raise ConnectionError(f"Failed to connect to Slack: {e.response['error']}")
    
    def send_drift_alert(
        self, 
        drift_result: DriftResult, 
        feature: str, 
        channel: Optional[str] = None,
        additional_context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send a drift detection alert to Slack.
        
        Args:
            drift_result: The DriftResult object containing drift information
            feature: Name of the feature that drifted
            channel: Channel to send the message (overrides default)
            additional_context: Additional context information to include
            
        Returns:
            bool: True if message was sent successfully, False otherwise
        """
        target_channel = channel or self.channel
        
        # Create alert message
        status_emoji = "🚨" if drift_result.is_drifted else "✅"
        status_text = "DRIFT DETECTED" if drift_result.is_drifted else "NO DRIFT"
        
        # Build the message blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} ETSI Watchdog Alert: {status_text}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Feature:* `{feature}`"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Method:* {drift_result.method.upper()}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Score:* {drift_result.score:.4f}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Threshold:* {drift_result.threshold}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Sample Size:* {drift_result.sample_size}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Timestamp:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                ]
            }
        ]
        
        # Add additional context if provided
        if additional_context:
            context_fields = []
            for key, value in additional_context.items():
                context_fields.append({
                    "type": "mrkdwn",
                    "text": f"*{key}:* {value}"
                })
            
            if context_fields:
                blocks.append({
                    "type": "section",
                    "fields": context_fields
                })
        
        # Add severity-based styling
        if drift_result.is_drifted:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":warning: *Action Required:* Data drift has been detected and may require immediate attention."
                }
            })
        
        try:
            response = self.client.chat_postMessage(
                channel=target_channel,
                blocks=blocks,
                text=f"ETSI Watchdog Alert: {status_text} for feature '{feature}'"  # Fallback text
            )
            return response["ok"]
        except SlackApiError as e:
            print(f"[etsi-watchdog] Failed to send Slack alert: {e.response['error']}")
            return False
    
    def send_system_alert(
        self, 
        message: str, 
        severity: str = "info",
        channel: Optional[str] = None,
        additional_details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send a general system alert to Slack.
        
        Args:
            message: The alert message
            severity: Alert severity level (info, warning, error, critical)
            channel: Channel to send the message (overrides default)
            additional_details: Additional details to include
            
        Returns:
            bool: True if message was sent successfully, False otherwise
        """
        target_channel = channel or self.channel
        
        # Map severity to emojis and colors
        severity_config = {
            "info": {"emoji": "ℹ️", "color": "#36a64f"},
            "warning": {"emoji": "⚠️", "color": "#ffcc00"},
            "error": {"emoji": "❌", "color": "#ff0000"},
            "critical": {"emoji": "🚨", "color": "#800000"}
        }
        
        config = severity_config.get(severity.lower(), severity_config["info"])
        
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{config['emoji']} *ETSI Watchdog System Alert*\n{message}"
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Severity: *{severity.upper()}* | Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                ]
            }
        ]
        
        # Add additional details if provided
        if additional_details:
            details_text = "\n".join([f"*{k}:* {v}" for k, v in additional_details.items()])
            blocks.insert(1, {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": details_text
                }
            })
        
        try:
            response = self.client.chat_postMessage(
                channel=target_channel,
                blocks=blocks,
                text=f"ETSI Watchdog System Alert: {message}"  # Fallback text
            )
            return response["ok"]
        except SlackApiError as e:
            print(f"[etsi-watchdog] Failed to send system alert: {e.response['error']}")
            return False
    
    def send_monitoring_summary(
        self,
        results: Dict[str, DriftResult],
        channel: Optional[str] = None,
        monitoring_context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send a summary of monitoring results to Slack.
        
        Args:
            results: Dictionary of feature names to DriftResult objects
            channel: Channel to send the message (overrides default)
            monitoring_context: Additional monitoring context
            
        Returns:
            bool: True if message was sent successfully, False otherwise
        """
        target_channel = channel or self.channel
        
        total_features = len(results)
        drifted_features = sum(1 for result in results.values() if result.is_drifted)
        
        # Determine overall status
        if drifted_features == 0:
            status_emoji = "✅"
            status_text = "ALL CLEAR"
        elif drifted_features < total_features / 2:
            status_emoji = "⚠️"
            status_text = "PARTIAL DRIFT"
        else:
            status_emoji = "🚨"
            status_text = "MULTIPLE DRIFTS"
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} ETSI Watchdog Monitoring Summary"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Status:* {status_text}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Features Monitored:* {total_features}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Features with Drift:* {drifted_features}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Timestamp:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                ]
            }
        ]
        
        # Add details for drifted features
        if drifted_features > 0:
            drift_details = []
            for feature, result in results.items():
                if result.is_drifted:
                    drift_details.append(
                        f"• `{feature}`: {result.method.upper()} score {result.score:.4f} (threshold: {result.threshold})"
                    )
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Drifted Features:*\n" + "\n".join(drift_details)
                }
            })
        
        # Add monitoring context if provided
        if monitoring_context:
            context_text = "\n".join([f"*{k}:* {v}" for k, v in monitoring_context.items()])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Monitoring Context:*\n{context_text}"
                }
            })
        
        try:
            response = self.client.chat_postMessage(
                channel=target_channel,
                blocks=blocks,
                text=f"ETSI Watchdog Monitoring Summary: {status_text}"  # Fallback text
            )
            return response["ok"]
        except SlackApiError as e:
            print(f"[etsi-watchdog] Failed to send monitoring summary: {e.response['error']}")
            return False
