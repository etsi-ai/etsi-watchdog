# example_slack_integration.py

"""
Example script demonstrating Slack alert integration with ETSI Watchdog.

This script shows how to:
1. Set up Slack notifications using environment variables
2. Configure monitoring with Slack alerts
3. Simulate drift detection and alert sending
4. Use the quick setup function for easy configuration

Before running this script:
1. Install the slack_sdk: pip install slack_sdk
2. Set environment variables:
   export SLACK_BOT_TOKEN="xoxb-your-bot-token"
   export SLACK_CHANNEL="#your-channel"
3. Ensure your Slack bot has permission to post to the channel

For testing without a real Slack workspace, you can modify the script
to use mock objects or disable Slack notifications.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Import ETSI Watchdog components
from etsi.watchdog import Monitor, SlackNotifier, WatchdogConfig, quick_setup
from etsi.watchdog.drift.base import DriftResult


def create_sample_data():
    """Create sample datasets for demonstration."""
    np.random.seed(42)
    
    # Reference dataset (baseline)
    reference_df = pd.DataFrame({
        'age': np.random.normal(35, 10, 1000),
        'income': np.random.normal(50000, 15000, 1000),
        'credit_score': np.random.normal(650, 100, 1000),
        'experience_years': np.random.exponential(5, 1000)
    })
    
    # Clean up the data
    reference_df['age'] = np.clip(reference_df['age'], 18, 80)
    reference_df['income'] = np.clip(reference_df['income'], 20000, 200000)
    reference_df['credit_score'] = np.clip(reference_df['credit_score'], 300, 850)
    reference_df['experience_years'] = np.clip(reference_df['experience_years'], 0, 40)
    
    # Current dataset (with some drift)
    current_df = pd.DataFrame({
        'age': np.random.normal(42, 12, 800),  # Slight drift in age
        'income': np.random.normal(55000, 18000, 800),  # Slight drift in income
        'credit_score': np.random.normal(620, 120, 800),  # Noticeable drift in credit score
        'experience_years': np.random.exponential(7, 800)  # Drift in experience
    })
    
    # Clean up the current data
    current_df['age'] = np.clip(current_df['age'], 18, 80)
    current_df['income'] = np.clip(current_df['income'], 20000, 200000)
    current_df['credit_score'] = np.clip(current_df['credit_score'], 300, 850)
    current_df['experience_years'] = np.clip(current_df['experience_years'], 0, 40)
    
    # Heavily drifted dataset for critical alert demonstration
    drifted_df = pd.DataFrame({
        'age': np.random.normal(55, 15, 500),  # Significant drift
        'income': np.random.normal(80000, 25000, 500),  # Significant drift
        'credit_score': np.random.normal(550, 150, 500),  # Significant drift
        'experience_years': np.random.exponential(12, 500)  # Significant drift
    })
    
    # Clean up the drifted data
    drifted_df['age'] = np.clip(drifted_df['age'], 18, 80)
    drifted_df['income'] = np.clip(drifted_df['income'], 20000, 200000)
    drifted_df['credit_score'] = np.clip(drifted_df['credit_score'], 300, 850)
    drifted_df['experience_years'] = np.clip(drifted_df['experience_years'], 0, 40)
    
    return reference_df, current_df, drifted_df


def example_basic_slack_setup():
    """Example 1: Basic Slack setup and monitoring."""
    print("\n" + "="*60)
    print("EXAMPLE 1: Basic Slack Setup and Monitoring")
    print("="*60)
    
    # Create sample data
    reference_df, current_df, _ = create_sample_data()
    
    # Method 1: Direct SlackNotifier usage
    try:
        print("\n1. Testing direct SlackNotifier...")
        notifier = SlackNotifier()
        print("✓ SlackNotifier initialized successfully")
        
        # Send a test system alert
        success = notifier.send_system_alert(
            message="ETSI Watchdog Slack integration test - system is operational",
            severity="info",
            additional_details={
                "test_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "environment": "development"
            }
        )
        
        if success:
            print("✓ Test system alert sent successfully")
        else:
            print("❌ Failed to send test system alert")
            
    except Exception as e:
        print(f"❌ SlackNotifier setup failed: {e}")
        print("   Make sure SLACK_BOT_TOKEN and SLACK_CHANNEL are set")
        return
    
    # Method 2: Monitor with Slack integration
    print("\n2. Setting up Monitor with Slack alerts...")
    monitor = Monitor(reference_df)
    monitor.enable_logging("logs/drift_with_slack.csv")
    monitor.enable_slack_alerts()
    
    if monitor.slack_enabled:
        print("✓ Monitor Slack alerts enabled")
        
        # Perform monitoring
        print("\n3. Running drift detection...")
        results = monitor.watch(
            current_df,
            threshold=0.15,
            slack_context={
                "model_name": "credit_risk_model_v2",
                "environment": "production",
                "batch_id": f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            }
        )
        
        # Display results
        print(f"✓ Monitoring completed. {len(results)} features analyzed")
        for feature, result in results.items():
            status = "DRIFT" if result.is_drifted else "OK"
            print(f"   - {feature}: {status} (score: {result.score:.4f})")
    else:
        print("❌ Failed to enable Slack alerts on Monitor")


def example_configuration_helper():
    """Example 2: Using the WatchdogConfig helper."""
    print("\n" + "="*60)
    print("EXAMPLE 2: Using WatchdogConfig Helper")
    print("="*60)
    
    reference_df, current_df, drifted_df = create_sample_data()
    
    # Create configuration
    config = WatchdogConfig()
    
    # Setup components
    print("\n1. Setting up configuration...")
    
    # Setup Slack
    slack_success = config.setup_slack(
        channel="#ml-drift-alerts",  # Override default channel
        send_summaries=True,
        send_individual_alerts=True
    )
    
    if slack_success:
        print("✓ Slack configuration setup successful")
    else:
        print("❌ Slack configuration setup failed")
        return
    
    # Setup logging
    config.setup_logging("logs/comprehensive_drift.json", "json")
    print("✓ Logging configuration setup")
    
    # Setup monitoring defaults
    config.setup_monitoring(
        default_algorithm="psi",
        default_threshold=0.1,
        default_features=["age", "income", "credit_score"]
    )
    print("✓ Monitoring configuration setup")
    
    # Print configuration summary
    config.print_configuration_summary()
    
    # Create configured monitor
    print("\n2. Creating configured monitor...")
    monitor = config.create_monitor(reference_df)
    
    # Test with mildly drifted data
    print("\n3. Testing with current data (mild drift expected)...")
    monitoring_params = config.get_monitoring_params()
    
    results1 = monitor.watch(
        current_df,
        features=monitoring_params['features'],
        algorithm=monitoring_params['algorithm'],
        threshold=monitoring_params['threshold'],
        slack_context={
            "analysis_type": "scheduled_check",
            "data_source": "production_pipeline"
        }
    )
    
    # Test with heavily drifted data
    print("\n4. Testing with heavily drifted data (alerts expected)...")
    results2 = monitor.watch(
        drifted_df,
        features=monitoring_params['features'],
        algorithm=monitoring_params['algorithm'],
        threshold=monitoring_params['threshold'],
        slack_context={
            "analysis_type": "critical_check",
            "data_source": "anomalous_batch",
            "requires_attention": True
        }
    )
    
    print(f"✓ Configuration-based monitoring completed")


def example_quick_setup():
    """Example 3: Using the quick_setup convenience function."""
    print("\n" + "="*60)
    print("EXAMPLE 3: Quick Setup Function")
    print("="*60)
    
    reference_df, current_df, drifted_df = create_sample_data()
    
    # Quick setup with all features
    print("\n1. Using quick_setup function...")
    
    try:
        config, monitor = quick_setup(
            reference_df=reference_df,
            slack_channel="#quick-alerts",
            log_path="logs/quick_setup_drift.csv"
        )
        
        print("✓ Quick setup completed successfully")
        
        # Show configuration
        print("\n2. Configuration summary:")
        config.print_configuration_summary()
        
        # Perform monitoring
        print("\n3. Running quick monitoring test...")
        results = monitor.watch(
            drifted_df,  # Use heavily drifted data for dramatic effect
            threshold=0.08,  # Lower threshold for more sensitive detection
            slack_context={
                "setup_type": "quick_setup",
                "test_run": True
            }
        )
        
        print(f"✓ Quick monitoring completed with {len(results)} features")
        
    except Exception as e:
        print(f"❌ Quick setup failed: {e}")


def example_rolling_monitoring():
    """Example 4: Rolling monitoring with Slack notifications."""
    print("\n" + "="*60)
    print("EXAMPLE 4: Rolling Monitoring with Slack")
    print("="*60)
    
    # Create time-series data
    print("\n1. Creating time-series data...")
    
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=30, freq='D')
    
    # Reference data
    reference_df = pd.DataFrame({
        'feature1': np.random.normal(0, 1, 1000),
        'feature2': np.random.normal(10, 2, 1000)
    })
    
    # Create rolling data with gradual drift
    rolling_data = []
    for i, date in enumerate(dates):
        # Gradually introduce drift
        drift_factor = i * 0.1
        daily_data = pd.DataFrame({
            'feature1': np.random.normal(drift_factor, 1, 50),
            'feature2': np.random.normal(10 + drift_factor, 2, 50)
        }, index=pd.date_range(date, periods=50, freq='H'))
        rolling_data.append(daily_data)
    
    rolling_df = pd.concat(rolling_data)
    print(f"✓ Created rolling dataset with {len(rolling_df)} data points over {len(dates)} days")
    
    # Setup monitor
    print("\n2. Setting up rolling monitor...")
    monitor = Monitor(reference_df)
    monitor.enable_slack_alerts(
        channel="#rolling-alerts",
        send_summaries=True,
        send_individual_alerts=False  # Reduce noise for rolling analysis
    )
    
    if not monitor.slack_enabled:
        print("❌ Slack not enabled, skipping rolling example")
        return
    
    # Perform rolling analysis
    print("\n3. Running rolling drift analysis...")
    try:
        results = monitor.watch_rolling(
            rolling_df,
            window=100,  # 100-hour windows
            freq='5D',   # Every 5 days
            threshold=0.15,
            slack_context={
                "analysis_type": "rolling_monitoring",
                "window_size": "100 hours",
                "frequency": "5 days"
            }
        )
        
        print(f"✓ Rolling analysis completed: {len(results)} time periods analyzed")
        
        # Show summary
        periods_with_drift = sum(1 for _, period_results in results 
                               if any(r.is_drifted for r in period_results.values()))
        print(f"   - Periods with drift: {periods_with_drift}/{len(results)}")
        
    except Exception as e:
        print(f"❌ Rolling analysis failed: {e}")


def example_error_scenarios():
    """Example 5: Demonstrating error handling and system alerts."""
    print("\n" + "="*60)
    print("EXAMPLE 5: Error Handling and System Alerts")
    print("="*60)
    
    reference_df, _, _ = create_sample_data()
    
    # Setup monitor
    monitor = Monitor(reference_df)
    monitor.enable_slack_alerts(channel="#error-alerts")
    
    if not monitor.slack_enabled:
        print("❌ Slack not enabled, skipping error scenarios")
        return
    
    print("\n1. Testing system alert for invalid data...")
    
    # Test with invalid data (should trigger error handling)
    try:
        invalid_df = pd.DataFrame({
            'wrong_feature': [1, 2, 3],  # Different features than reference
            'another_wrong': [4, 5, 6]
        })
        
        monitor.watch(invalid_df, slack_context={"test": "error_scenario"})
        
    except Exception as e:
        print(f"✓ Error properly handled: {str(e)[:50]}...")
    
    print("\n2. Sending custom system alerts...")
    
    if monitor.slack_notifier:
        # Send different severity alerts
        severities = ["info", "warning", "error", "critical"]
        
        for severity in severities:
            success = monitor.slack_notifier.send_system_alert(
                message=f"Test {severity} alert from ETSI Watchdog",
                severity=severity,
                additional_details={
                    "timestamp": datetime.now().isoformat(),
                    "test_case": "severity_demonstration"
                }
            )
            
            if success:
                print(f"✓ {severity.upper()} alert sent successfully")
            else:
                print(f"❌ Failed to send {severity} alert")


def main():
    """Main function to run all examples."""
    print("ETSI Watchdog Slack Integration Examples")
    print("=" * 80)
    
    # Check environment setup
    slack_token = os.getenv('SLACK_BOT_TOKEN')
    slack_channel = os.getenv('SLACK_CHANNEL')
    
    if not slack_token:
        print("\n⚠️  WARNING: SLACK_BOT_TOKEN environment variable not set")
        print("   Set it with: export SLACK_BOT_TOKEN='xoxb-your-token'")
        print("   Some examples may fail without proper Slack configuration\n")
    
    if not slack_channel:
        print("📝 INFO: SLACK_CHANNEL not set, will use default '#alerts'")
        print("   Set it with: export SLACK_CHANNEL='#your-channel'\n")
    
    try:
        # Run examples
        example_basic_slack_setup()
        example_configuration_helper()
        example_quick_setup()
        example_rolling_monitoring()
        example_error_scenarios()
        
        print("\n" + "="*80)
        print("✅ All examples completed!")
        print("   Check your Slack channel for notifications")
        print("   Check logs/ directory for drift logs")
        print("="*80)
        
    except KeyboardInterrupt:
        print("\n\n⏹️  Examples interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        print("   Check your Slack configuration and try again")


if __name__ == "__main__":
    main()
