# ETSI Watchdog Slack Integration

This document provides comprehensive information about the Slack alert notification feature for ETSI Watchdog.

## Overview

The Slack integration feature enables real-time notifications when data drift is detected by the ETSI Watchdog monitoring system. This ensures that teams can respond swiftly to data quality issues and maintain transparency across operations.

## Features

- ✅ **Real-time drift alerts** - Immediate notifications when drift is detected
- ✅ **System status alerts** - Notifications for system errors and operational status
- ✅ **Monitoring summaries** - Periodic summaries of monitoring results
- ✅ **Rich message formatting** - Structured Slack messages with detailed information
- ✅ **Configurable severity levels** - Different alert types (info, warning, error, critical)
- ✅ **Environment variable configuration** - Secure token and channel management
- ✅ **Error handling** - Graceful fallback when Slack is unavailable

## Installation

1. Install the required dependency:
```bash
pip install slack_sdk
```

2. Ensure you have the latest version of etsi-watchdog that includes Slack support:
```bash
pip install etsi-watchdog>=0.2.2
```

## Slack App Setup

### 1. Create a Slack App

1. Go to [Slack API](https://api.slack.com/apps)
2. Click "Create New App"
3. Choose "From scratch"
4. Name your app (e.g., "ETSI Watchdog")
5. Select your workspace

### 2. Configure Bot Permissions

Under "OAuth & Permissions", add these Bot Token Scopes:
- `chat:write` - Send messages to channels
- `chat:write.public` - Send messages to public channels without joining

### 3. Install App to Workspace

1. Click "Install to Workspace"
2. Authorize the app
3. Copy the "Bot User OAuth Token" (starts with `xoxb-`)

### 4. Add Bot to Channel

Invite the bot to your desired channel:
```
/invite @etsi-watchdog
```

## Configuration

### Environment Variables

Set up your environment with the required variables:

```bash
export SLACK_BOT_TOKEN="xoxb-your-bot-token-here"
export SLACK_CHANNEL="#your-alerts-channel"
```

### Configuration Options

The Slack integration can be configured in several ways:

1. **Environment variables** (recommended for production)
2. **Direct parameter passing** (good for testing)
3. **Configuration helper class** (best for complex setups)

## Usage Examples

### Basic Usage

```python
import pandas as pd
from etsi.watchdog import Monitor

# Your reference and current data
reference_df = pd.DataFrame({"feature1": [1, 2, 3], "feature2": [10, 20, 30]})
current_df = pd.DataFrame({"feature1": [4, 5, 6], "feature2": [40, 50, 60]})

# Create monitor and enable Slack alerts
monitor = Monitor(reference_df)
monitor.enable_slack_alerts()

# Run monitoring - alerts will be sent automatically if drift is detected
results = monitor.watch(current_df, threshold=0.2)
```

### Advanced Configuration

```python
from etsi.watchdog import WatchdogConfig

# Create configuration
config = WatchdogConfig()

# Setup Slack with custom settings
config.setup_slack(
    channel="#ml-alerts",
    send_summaries=True,
    send_individual_alerts=True
)

# Setup logging
config.setup_logging("logs/drift.json", "json")

# Create configured monitor
monitor = config.create_monitor(reference_df)

# Run monitoring with context
results = monitor.watch(
    current_df,
    threshold=0.15,
    slack_context={
        "model_name": "fraud_detection_v2",
        "environment": "production",
        "batch_id": "batch_20240122_0800"
    }
)
```

### Quick Setup

```python
from etsi.watchdog import quick_setup

# One-line setup with all features
config, monitor = quick_setup(
    reference_df=reference_df,
    slack_channel="#quick-alerts",
    log_path="logs/drift.csv"
)

# Ready to use
results = monitor.watch(current_df)
```

### Rolling Monitoring

```python
import pandas as pd
from datetime import datetime

# Time-series data with datetime index
dates = pd.date_range('2024-01-01', periods=100, freq='H')
time_series_df = pd.DataFrame({
    'feature1': range(100),
    'feature2': range(200, 300)
}, index=dates)

# Rolling analysis with Slack notifications
results = monitor.watch_rolling(
    time_series_df,
    window=24,  # 24-hour windows
    freq='D',   # Daily analysis
    threshold=0.2,
    slack_context={
        "analysis_type": "rolling_24h",
        "frequency": "daily"
    }
)
```

### Direct SlackNotifier Usage

```python
from etsi.watchdog import SlackNotifier, DriftResult

# Create notifier
notifier = SlackNotifier(channel="#custom-alerts")

# Send custom system alert
notifier.send_system_alert(
    message="Model deployment completed successfully",
    severity="info",
    additional_details={
        "version": "v2.1.0",
        "deployment_time": "2024-01-22 14:30:00"
    }
)

# Send drift alert (usually done automatically by Monitor)
drift_result = DriftResult(
    method="psi",
    score=0.85,
    threshold=0.2,
    details={},
    sample_size=1000
)

notifier.send_drift_alert(
    drift_result=drift_result,
    feature="customer_age",
    additional_context={"model": "churn_prediction"}
)
```

## Message Types

### Drift Detection Alert

Sent when data drift is detected on a specific feature:

- 🚨 **Header**: "DRIFT DETECTED" or ✅ "NO DRIFT"
- **Feature information**: Name, algorithm, score, threshold
- **Context**: Additional details provided via `slack_context`
- **Action required note**: For drifted features

### Monitoring Summary

Periodic summary of all monitored features:

- **Overall status**: ALL CLEAR, PARTIAL DRIFT, or MULTIPLE DRIFTS
- **Statistics**: Total features, features with drift
- **Detailed breakdown**: List of drifted features with scores
- **Monitoring context**: Batch information, timestamps

### System Alert

General system notifications:

- **Severity levels**: Info ℹ️, Warning ⚠️, Error ❌, Critical 🚨
- **Message content**: Custom alert message
- **Additional details**: System information, error context
- **Timestamp**: When the alert was generated

## Configuration Reference

### WatchdogConfig Methods

```python
config = WatchdogConfig()

# Slack configuration
config.setup_slack(
    token=None,                    # Slack bot token (optional if env var is set)
    channel=None,                  # Channel name (optional if env var is set)
    send_summaries=True,           # Send monitoring summaries
    send_individual_alerts=True,   # Send individual drift alerts
    validate_connection=True       # Test connection on setup
)

# Logging configuration
config.setup_logging(
    log_path="logs/drift.csv",     # Path to log file
    log_format="csv"               # Format: "csv" or "json"
)

# Monitoring defaults
config.setup_monitoring(
    default_algorithm="psi",       # Default drift algorithm
    default_threshold=0.2,         # Default threshold
    default_features=None          # Default features (None = all)
)
```

### Monitor Slack Methods

```python
monitor = Monitor(reference_df)

# Enable Slack alerts
monitor.enable_slack_alerts(
    token=None,                    # Optional token override
    channel=None,                  # Optional channel override
    send_summaries=True,           # Send summaries
    send_individual_alerts=True    # Send individual alerts
)

# Disable Slack alerts
monitor.disable_slack_alerts()

# Check if Slack is enabled
if monitor.slack_enabled:
    print("Slack alerts are active")
```

## Error Handling

The Slack integration includes comprehensive error handling:

### Connection Errors

- **Validation on setup**: Connection is tested when SlackNotifier is initialized
- **Graceful degradation**: Monitoring continues even if Slack fails
- **Error logging**: Failed Slack operations are logged to console

### Configuration Errors

- **Missing tokens**: Clear error messages when tokens are not provided
- **Invalid channels**: Slack API errors are caught and reported
- **Permission issues**: Bot permission problems are identified

### Runtime Errors

- **Network issues**: Transient network problems don't stop monitoring
- **Rate limiting**: Slack rate limits are respected (handled by slack_sdk)
- **Message failures**: Individual message failures don't affect other alerts

## Best Practices

### Security

1. **Use environment variables** for tokens in production
2. **Restrict bot permissions** to only required scopes
3. **Rotate tokens regularly** as per your security policy
4. **Don't commit tokens** to version control

### Performance

1. **Configure alert frequency** to avoid noise
2. **Use summaries** for high-frequency monitoring
3. **Set appropriate thresholds** to balance sensitivity and noise
4. **Monitor Slack API usage** to stay within rate limits

### Operations

1. **Test in development** before deploying to production
2. **Monitor the monitoring** - ensure alerts are being sent
3. **Have fallback plans** for when Slack is unavailable
4. **Document your configuration** for team members

## Troubleshooting

### Common Issues

**"Failed to connect to Slack: invalid_auth"**
- Check your bot token is correct
- Ensure token starts with `xoxb-`
- Verify the token hasn't expired

**"Failed to send Slack alert: channel_not_found"**
- Verify channel name includes `#` prefix
- Ensure bot is added to the channel
- Check channel exists and is accessible

**"Slack token must be provided"**
- Set `SLACK_BOT_TOKEN` environment variable
- Or pass token directly to SlackNotifier

**No alerts being sent despite drift detection**
- Check `monitor.slack_enabled` is `True`
- Verify drift threshold is appropriate
- Check console for error messages

### Testing Connection

```python
from etsi.watchdog import SlackNotifier

try:
    notifier = SlackNotifier()
    print("✓ Slack connection successful")
    
    # Send test message
    success = notifier.send_system_alert("Test message from ETSI Watchdog")
    if success:
        print("✓ Test message sent successfully")
    else:
        print("❌ Failed to send test message")
        
except Exception as e:
    print(f"❌ Slack setup failed: {e}")
```

### Debugging

Enable verbose logging to see detailed information:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Your monitoring code here
```

## API Reference

### SlackNotifier Class

#### Constructor
```python
SlackNotifier(token=None, channel=None)
```

#### Methods

**send_drift_alert(drift_result, feature, channel=None, additional_context=None)**
- Send drift detection alert
- Returns: `bool` - Success status

**send_system_alert(message, severity="info", channel=None, additional_details=None)**
- Send system alert with specified severity
- Returns: `bool` - Success status

**send_monitoring_summary(results, channel=None, monitoring_context=None)**
- Send summary of monitoring results
- Returns: `bool` - Success status

### WatchdogConfig Class

#### Methods

**setup_slack(token=None, channel=None, send_summaries=True, send_individual_alerts=True, validate_connection=True)**
- Configure Slack integration
- Returns: `bool` - Success status

**create_monitor(reference_df)**
- Create configured Monitor instance
- Returns: `Monitor` - Configured monitor

**validate_environment()**
- Check environment setup
- Returns: `dict` - Validation results

**print_configuration_summary()**
- Print current configuration status

## Examples and Testing

See `example_slack_integration.py` for comprehensive usage examples and `test/test_slack_notifier.py` for unit tests.

To run the examples:
```bash
export SLACK_BOT_TOKEN="your-token"
export SLACK_CHANNEL="#your-channel"
python example_slack_integration.py
```

To run the tests:
```bash
python -m pytest test/test_slack_notifier.py -v
```
