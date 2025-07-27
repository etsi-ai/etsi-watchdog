import os
import pandas as pd
import base64
from io import BytesIO
import matplotlib.pyplot as plt
import seaborn as sns
from jinja2 import Environment, FileSystemLoader
# ❌ Removed: from weasyprint import HTML


def get_template_env():
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    return Environment(loader=FileSystemLoader(template_dir))


def plot_feature_distribution(feature, ref_series, live_series):
    """Returns base64-encoded KDE plot for the given feature."""
    plt.figure(figsize=(6, 4))
    sns.kdeplot(ref_series, label="Reference", fill=True)
    sns.kdeplot(live_series, label="Live", fill=True)
    plt.title(f"Drift in '{feature}'")
    plt.legend()
    buf = BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def generate_drift_report(
    ref_df,
    live_df,
    drift_results,
    output_path="drift_report.html",
    format="html",
    metadata=None,
):
    """Generate a professional drift report in HTML or PDF."""
    # Prepare drift summary table
    df = pd.DataFrame.from_dict(drift_results, orient="index")
    df.reset_index(inplace=True)
    df.columns = ["Feature", "P-Value", "Statistic", "Drift"]
    drift_table_html = df.to_html(index=False, classes="table", border=0)

    # Extract alert features
    alerts = df[df["Drift"] == True]["Feature"].tolist()

    # Plot visualizations
    plots = []
    for feature in df["Feature"]:
        if feature in ref_df.columns and feature in live_df.columns:
            try:
                plot = plot_feature_distribution(ref_df[feature], live_df[feature])
                plots.append(plot)
            except Exception as e:
                print(f"Warning: Skipping plot for {feature} due to {e}")

    # Load template
    env = get_template_env()
    template = env.get_template("report_template.html.j2")

    summary_text = (
        metadata.get("summary")
        if metadata and "summary" in metadata
        else "This drift report was auto-generated."
    )

    html_out = template.render(
        summary=summary_text,
        drift_table=drift_table_html,
        alerts=alerts,
        plots=plots,
        metadata=metadata or {},
    )

    # Write HTML
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    # Optional PDF export
    if format.lower() == "pdf":
        from weasyprint import HTML  # ✅ Lazy import to prevent DLL crash
        pdf_path = output_path.replace(".html", ".pdf")
        HTML(string=html_out).write_pdf(pdf_path)
        print(f"PDF report saved to {pdf_path}")
    else:
        print(f"HTML report saved to {output_path}")
