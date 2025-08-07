# etsi/watchdog/cli.py

import click
import os
from .config.parser import ConfigParser
from .config.runner import ConfigRunner
from .config.schema import DriftConfig, AlgorithmConfig, OutputConfig

@click.group()
def cli():
    """etsi-watchdog: A tool for real-time data drift detection."""
    pass

@cli.command(name="from-config")
@click.option('--config', '-c', required=True, type=click.Path(exists=True, dir_okay=False), help="Path to the YAML configuration file.")
def from_config_command(config):
    """Run drift detection from a YAML configuration file."""
    print(f"Loading configuration from: {config}")
    parser = ConfigParser()
    config_obj = parser.load_config(config)
    
    runner = ConfigRunner()
    runner.run(config_obj) 
    print("✅ Done.")

@cli.command(name="detect")
@click.option('--ref', required=True, type=click.Path(exists=True), help="Path to reference CSV.")
@click.option('--current', required=True, type=click.Path(exists=True), help="Path to current CSV.")
@click.option('--features', required=True, type=str, help="Comma-separated list of features.")
@click.option('--output-path', default="drift_report", help="Base path for the output report (e.g., 'drift_report').")
@click.option('--algo', default="psi", help="Drift algorithm (e.g., psi, ks).")
@click.option('--threshold', default=0.2, type=float, help="Drift threshold.")
@click.option('--report', type=click.Choice(['pdf', 'html']), help="Generate a visual report in the specified format instead of JSON.")
def detect_command(ref, current, features, output_path, algo, threshold, report):
    """Run a quick drift detection with direct arguments."""
    print("Building configuration from CLI arguments...")

    # Determine output configuration based on the --report flag
    if report:
        # User requested a visual report (pdf or html)
        output_format = report
        final_output_path = f"{output_path}.{report}"
        visualizations = True
    else:
        # Default to JSON output
        output_format = "json"
        final_output_path = f"{output_path}.json"
        visualizations = False

    # Create the configuration object for the runner
    drift_config = DriftConfig(
        reference_data=ref,
        current_data=current,
        features=[f.strip() for f in features.split(',')],
        algorithms=[AlgorithmConfig(name=algo, threshold=threshold)],
        output=OutputConfig(
            format=output_format, 
            path=final_output_path, 
            visualizations=visualizations
        ),
        monitoring=None 
    )

    runner = ConfigRunner()
    runner.run(drift_config) 
    print(f"✅ Report generated at: {final_output_path}")

if __name__ == "__main__":
    cli()