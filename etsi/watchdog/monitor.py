# etsi/watchdog/monitor.py

from .drift_check import DriftCheck
from .logger import log_drift
from .slack_notifier import SlackNotifier
import pandas as pd
from typing import Optional, Dict, Any


class Monitor:
    """
    Monitor — Periodic/rolling drift monitoring with Slack alert integration.

    Example:
    >>> monitor = Monitor(reference_df)
    >>> monitor.enable_logging("logs/drift.csv")
    >>> monitor.enable_slack_alerts()  # Uses environment variables
    >>> monitor.watch(live_df)
    """

    def __init__(self, reference_df):
        self.reference = reference_df
        self.log_path = None
        self.slack_notifier = None
        self.slack_enabled = False

    def enable_logging(self, path: str):
        """Enable drift logging to a file."""
        self.log_path = path

    def enable_slack_alerts(
        self, 
        token: Optional[str] = None, 
        channel: Optional[str] = None,
        send_summaries: bool = True,
        send_individual_alerts: bool = True
    ):
        """
        Enable Slack alert notifications.
        
        Args:
            token: Slack bot token (if not provided, uses SLACK_BOT_TOKEN env var)
            channel: Default channel to send messages (if not provided, uses SLACK_CHANNEL env var)
            send_summaries: Whether to send monitoring summaries
            send_individual_alerts: Whether to send alerts for individual drift detections
        """
        try:
            self.slack_notifier = SlackNotifier(token=token, channel=channel)
            self.slack_enabled = True
            self.send_summaries = send_summaries
            self.send_individual_alerts = send_individual_alerts
            print("[etsi-watchdog] Slack alerts enabled successfully")
        except Exception as e:
            print(f"[etsi-watchdog] Failed to enable Slack alerts: {e}")
            self.slack_enabled = False

    def disable_slack_alerts(self):
        """Disable Slack alert notifications."""
        self.slack_enabled = False
        self.slack_notifier = None
        print("[etsi-watchdog] Slack alerts disabled")

    def watch(self, live_df: pd.DataFrame, features=None, algorithm="psi", threshold=0.2, slack_context: Optional[Dict[str, Any]] = None):
        """
        Watch for drift in the live data with optional Slack notifications.
        
        Args:
            live_df: Current/live DataFrame to check for drift
            features: List of features to monitor (if None, uses all reference columns)
            algorithm: Drift detection algorithm to use
            threshold: Drift threshold
            slack_context: Additional context to include in Slack notifications
            
        Returns:
            Dictionary of drift results per feature
        """
        checker = DriftCheck(self.reference, algorithm=algorithm, threshold=threshold)
        features = features or list(self.reference.columns)
        results = {}
        
        try:
            results = checker.run(live_df, features=features)
            
            # Log results if logging is enabled
            for feat, res in results.items():
                if self.log_path:
                    log_drift(res, self.log_path, feature=feat)
                
                # Send individual Slack alerts if enabled
                if self.slack_enabled and self.send_individual_alerts and res.is_drifted:
                    self.slack_notifier.send_drift_alert(
                        drift_result=res,
                        feature=feat,
                        additional_context=slack_context
                    )
            
            # Send summary if Slack is enabled and configured for summaries
            if self.slack_enabled and self.send_summaries and results:
                monitoring_context = slack_context or {}
                monitoring_context.update({
                    "Algorithm": algorithm,
                    "Threshold": threshold,
                    "Data Points": len(live_df)
                })
                
                self.slack_notifier.send_monitoring_summary(
                    results=results,
                    monitoring_context=monitoring_context
                )
                
        except Exception as e:
            error_msg = f"Error during drift monitoring: {str(e)}"
            print(f"[etsi-watchdog] {error_msg}")
            
            # Send system alert if Slack is enabled
            if self.slack_enabled:
                self.slack_notifier.send_system_alert(
                    message=error_msg,
                    severity="error",
                    additional_details={
                        "Features": str(features),
                        "Algorithm": algorithm,
                        "Data Shape": str(live_df.shape)
                    }
                )
            raise
        
        return results

    def watch_rolling(
        self, 
        live_df: pd.DataFrame, 
        window=50, 
        freq="D", 
        features=None, 
        algorithm="psi", 
        threshold=0.2,
        slack_context: Optional[Dict[str, Any]] = None
    ):
        """
        Perform rolling drift detection with Slack notifications.
        
        Args:
            live_df: DataFrame with DatetimeIndex for rolling analysis
            window: Rolling window size
            freq: Frequency for grouping (e.g., 'D' for daily, 'H' for hourly)
            features: Features to monitor
            algorithm: Drift detection algorithm
            threshold: Drift threshold
            slack_context: Additional context for Slack notifications
            
        Returns:
            List of tuples containing (timestamp, results) for each time period
        """
        if not isinstance(live_df.index, pd.DatetimeIndex):
            error_msg = "Live data must have a DatetimeIndex for rolling drift detection."
            
            if self.slack_enabled:
                self.slack_notifier.send_system_alert(
                    message=error_msg,
                    severity="error"
                )
            
            raise ValueError(error_msg)

        results = []
        total_periods = 0
        periods_with_drift = 0
        
        try:
            for timestamp, group in live_df.groupby(pd.Grouper(freq=freq)):
                total_periods += 1
                
                if len(group) >= window:
                    chunk = group.tail(window)
                    period_context = (slack_context or {}).copy()
                    period_context.update({
                        "Timestamp": timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                        "Window Size": window,
                        "Frequency": freq
                    })
                    
                    # Temporarily disable summaries for individual periods
                    original_send_summaries = getattr(self, 'send_summaries', True)
                    self.send_summaries = False
                    
                    result = self.watch(
                        chunk, 
                        features=features, 
                        algorithm=algorithm, 
                        threshold=threshold,
                        slack_context=period_context
                    )
                    
                    # Restore summary setting
                    self.send_summaries = original_send_summaries
                    
                    results.append((timestamp, result))
                    
                    # Check if any drift was detected in this period
                    if any(res.is_drifted for res in result.values()):
                        periods_with_drift += 1
            
            # Send rolling monitoring summary
            if self.slack_enabled and self.send_summaries and results:
                summary_context = (slack_context or {}).copy()
                summary_context.update({
                    "Rolling Analysis": f"{total_periods} periods analyzed",
                    "Periods with Drift": f"{periods_with_drift}/{total_periods}",
                    "Window Size": window,
                    "Frequency": freq
                })
                
                # Create aggregate results for summary
                all_results = {}
                for _, period_results in results:
                    for feature, result in period_results.items():
                        if feature not in all_results:
                            all_results[feature] = result
                        elif result.is_drifted and not all_results[feature].is_drifted:
                            all_results[feature] = result
                
                self.slack_notifier.send_system_alert(
                    message=f"Rolling drift analysis completed: {periods_with_drift}/{total_periods} periods showed drift",
                    severity="warning" if periods_with_drift > 0 else "info",
                    additional_details=summary_context
                )
                
        except Exception as e:
            error_msg = f"Error during rolling drift monitoring: {str(e)}"
            print(f"[etsi-watchdog] {error_msg}")
            
            if self.slack_enabled:
                self.slack_notifier.send_system_alert(
                    message=error_msg,
                    severity="error",
                    additional_details={
                        "Window Size": window,
                        "Frequency": freq,
                        "Data Shape": str(live_df.shape)
                    }
                )
            raise
        
        return results
