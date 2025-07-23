# etsi/watchdog/config.py

import os
from typing import Optional, Dict, Any
from .slack_notifier import SlackNotifier


class WatchdogConfig:
    """
    Configuration helper for ETSI Watchdog with Slack integration.
    
    This class provides easy configuration management for the watchdog system,
    including Slack notifications, logging, and monitoring parameters.
    
    Example:
    >>> config = WatchdogConfig()
    >>> config.setup_slack(channel="#ml-alerts")
    >>> monitor = config.create_monitor(reference_df)
    """
    
    def __init__(self):
        self.slack_config = {}
        self.logging_config = {}
        self.monitoring_config = {}
    
    def setup_slack(
        self,
        token: Optional[str] = None,
        channel: Optional[str] = None,
        send_summaries: bool = True,
        send_individual_alerts: bool = True,
        validate_connection: bool = True
    ) -> bool:
        """
        Set up Slack configuration.
        
        Args:
            token: Slack bot token (if not provided, uses SLACK_BOT_TOKEN env var)
            channel: Default channel for notifications (if not provided, uses SLACK_CHANNEL env var)
            send_summaries: Whether to send monitoring summaries
            send_individual_alerts: Whether to send individual drift alerts
            validate_connection: Whether to validate the Slack connection immediately
            
        Returns:
            bool: True if setup was successful, False otherwise
        """
        try:
            self.slack_config = {
                'token': token or os.getenv('SLACK_BOT_TOKEN'),
                'channel': channel or os.getenv('SLACK_CHANNEL', '#alerts'),
                'send_summaries': send_summaries,
                'send_individual_alerts': send_individual_alerts
            }
            
            if validate_connection:
                # Test connection
                test_notifier = SlackNotifier(
                    token=self.slack_config['token'],
                    channel=self.slack_config['channel']
                )
                print("[etsi-watchdog] Slack connection validated successfully")
            
            return True
            
        except Exception as e:
            print(f"[etsi-watchdog] Failed to setup Slack configuration: {e}")
            self.slack_config = {}
            return False
    
    def setup_logging(self, log_path: str, log_format: str = "csv"):
        """
        Set up logging configuration.
        
        Args:
            log_path: Path to the log file
            log_format: Format for logging ("csv" or "json")
        """
        self.logging_config = {
            'path': log_path,
            'format': log_format
        }
    
    def setup_monitoring(
        self,
        default_algorithm: str = "psi",
        default_threshold: float = 0.2,
        default_features: Optional[list] = None
    ):
        """
        Set up default monitoring parameters.
        
        Args:
            default_algorithm: Default drift detection algorithm
            default_threshold: Default drift threshold
            default_features: Default features to monitor
        """
        self.monitoring_config = {
            'algorithm': default_algorithm,
            'threshold': default_threshold,
            'features': default_features
        }
    
    def create_monitor(self, reference_df):
        """
        Create a configured Monitor instance.
        
        Args:
            reference_df: Reference DataFrame for drift detection
            
        Returns:
            Monitor: Configured monitor instance
        """
        from .monitor import Monitor
        
        monitor = Monitor(reference_df)
        
        # Configure logging if set up
        if self.logging_config:
            monitor.enable_logging(self.logging_config['path'])
        
        # Configure Slack if set up
        if self.slack_config:
            monitor.enable_slack_alerts(
                token=self.slack_config['token'],
                channel=self.slack_config['channel'],
                send_summaries=self.slack_config['send_summaries'],
                send_individual_alerts=self.slack_config['send_individual_alerts']
            )
        
        return monitor
    
    def get_monitoring_params(self) -> Dict[str, Any]:
        """Get the default monitoring parameters."""
        return self.monitoring_config.copy()
    
    def is_slack_enabled(self) -> bool:
        """Check if Slack configuration is enabled."""
        return bool(self.slack_config.get('token'))
    
    def is_logging_enabled(self) -> bool:
        """Check if logging configuration is enabled."""
        return bool(self.logging_config.get('path'))
    
    def validate_environment(self) -> Dict[str, bool]:
        """
        Validate the environment setup for watchdog operation.
        
        Returns:
            Dict containing validation results for different components
        """
        validation_results = {
            'slack_token_available': bool(os.getenv('SLACK_BOT_TOKEN') or self.slack_config.get('token')),
            'slack_channel_configured': bool(os.getenv('SLACK_CHANNEL') or self.slack_config.get('channel')),
            'slack_connection_valid': False,
            'logging_configured': self.is_logging_enabled()
        }
        
        # Test Slack connection if token is available
        if validation_results['slack_token_available']:
            try:
                test_notifier = SlackNotifier(
                    token=self.slack_config.get('token') or os.getenv('SLACK_BOT_TOKEN'),
                    channel=self.slack_config.get('channel') or os.getenv('SLACK_CHANNEL', '#alerts')
                )
                validation_results['slack_connection_valid'] = True
            except Exception:
                validation_results['slack_connection_valid'] = False
        
        return validation_results
    
    def print_configuration_summary(self):
        """Print a summary of the current configuration."""
        print("\n" + "="*50)
        print("ETSI Watchdog Configuration Summary")
        print("="*50)
        
        # Slack configuration
        print("\n📱 Slack Configuration:")
        if self.slack_config:
            print(f"  ✓ Token: {'*' * 20 if self.slack_config.get('token') else 'Not set'}")
            print(f"  ✓ Channel: {self.slack_config.get('channel', 'Not set')}")
            print(f"  ✓ Send Summaries: {self.slack_config.get('send_summaries', False)}")
            print(f"  ✓ Send Individual Alerts: {self.slack_config.get('send_individual_alerts', False)}")
        else:
            print("  ❌ Not configured")
        
        # Logging configuration
        print("\n📝 Logging Configuration:")
        if self.logging_config:
            print(f"  ✓ Path: {self.logging_config.get('path', 'Not set')}")
            print(f"  ✓ Format: {self.logging_config.get('format', 'Not set')}")
        else:
            print("  ❌ Not configured")
        
        # Monitoring configuration
        print("\n🔍 Monitoring Configuration:")
        if self.monitoring_config:
            print(f"  ✓ Algorithm: {self.monitoring_config.get('algorithm', 'psi')}")
            print(f"  ✓ Threshold: {self.monitoring_config.get('threshold', 0.2)}")
            print(f"  ✓ Features: {self.monitoring_config.get('features', 'Auto-detect')}")
        else:
            print("  ❌ Using defaults")
        
        # Environment validation
        print("\n🔧 Environment Validation:")
        validation = self.validate_environment()
        for key, value in validation.items():
            status = "✓" if value else "❌"
            print(f"  {status} {key.replace('_', ' ').title()}: {value}")
        
        print("="*50)


# Convenience function for quick setup
def quick_setup(
    reference_df,
    slack_token: Optional[str] = None,
    slack_channel: Optional[str] = None,
    log_path: Optional[str] = None
):
    """
    Quick setup function for common use cases.
    
    Args:
        reference_df: Reference DataFrame for drift detection
        slack_token: Slack bot token
        slack_channel: Slack channel for notifications
        log_path: Path for logging drift results
        
    Returns:
        Tuple of (WatchdogConfig, Monitor) instances
    """
    config = WatchdogConfig()
    
    # Setup Slack if token is provided or available in environment
    if slack_token or os.getenv('SLACK_BOT_TOKEN'):
        config.setup_slack(token=slack_token, channel=slack_channel)
    
    # Setup logging if path is provided
    if log_path:
        config.setup_logging(log_path)
    
    # Setup default monitoring parameters
    config.setup_monitoring()
    
    # Create and return configured monitor
    monitor = config.create_monitor(reference_df)
    
    return config, monitor
