
import streamlit as st
import pandas as pd
from etsi.watchdog import DriftCheck


# --- Glassmorphism Theme Styling ---
st.markdown("""
<style>
body, .stApp {
    background: linear-gradient(135deg, #e3f2fd 0%, #90caf9 100%) !important;
}
.stSidebar, .css-1d391kg, .css-1v0mbdj, .css-1lcbmhc {
    background: rgba(255,255,255,0.15) !important;
    box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37) !important;
    backdrop-filter: blur(12px) !important;
    -webkit-backdrop-filter: blur(12px) !important;
    border-radius: 16px !important;
    border: 1px solid rgba(255,255,255,0.18) !important;
}
.glass-card {
    background: rgba(255,255,255,0.25);
    box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border-radius: 16px;
    border: 1px solid rgba(255,255,255,0.18);
    padding: 2rem;
    margin: 2rem auto;
    max-width: 500px;
}
.glass-btn {
    background: rgba(255,255,255,0.25) !important;
    color: #0d47a1 !important;
    border-radius: 8px !important;
    border: 1px solid rgba(255,255,255,0.18) !important;
    box-shadow: 0 4px 16px 0 rgba(31, 38, 135, 0.17) !important;
}
.glass-input {
    background: rgba(255,255,255,0.35) !important;
    color: #0d47a1 !important;
    border-radius: 8px !important;
    border: 1px solid rgba(255,255,255,0.18) !important;
}
</style>
""", unsafe_allow_html=True)

# --- Custom Logo ---
# st.markdown("""
# <div class='logo-animate glass-card'>
# <svg width="120" height="60" viewBox="0 0 120 60" fill="none" xmlns="http://www.w3.org/2000/svg">
#   <ellipse cx="60" cy="30" rx="55" ry="22" fill="#90caf9"/>
#   <ellipse cx="60" cy="30" rx="40" ry="16" fill="#e3f2fd"/>
#   <text x="50%" y="50%" text-anchor="middle" fill="#0d47a1" font-size="22" font-family="Arial" dy=".3em">ETSI</text>
# </svg>
# </div>
# """, unsafe_allow_html=True)

# --- Simple In-Memory User Store ---
if 'users' not in st.session_state:
    st.session_state['users'] = {'demo': 'demo123'}  # username: password
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'username' not in st.session_state:
    st.session_state['username'] = ''

def login_form():
    st.subheader("Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        if username in st.session_state['users'] and st.session_state['users'][username] == password:
            st.session_state['logged_in'] = True
            st.session_state['username'] = username
            st.success(f"Welcome, {username}!")
        else:
            st.error("Invalid username or password.")

def signup_form():
    st.subheader("Sign Up")
    new_username = st.text_input("New Username")
    new_password = st.text_input("New Password", type="password")
    if st.button("Sign Up"):
        if new_username in st.session_state['users']:
            st.error("Username already exists.")
        elif not new_username or not new_password:
            st.error("Please enter both username and password.")
        else:
            st.session_state['users'][new_username] = new_password
            st.success("Account created! Please log in.")

# --- Auth Page ---
if not st.session_state['logged_in']:
            st.markdown("""
            <div class='glass-card' style='max-width:400px;margin:3rem auto;text-align:center;'>
                <div style='margin-bottom:1rem;'>
                    <svg width='60' height='30' viewBox='0 0 120 60' fill='none' xmlns='http://www.w3.org/2000/svg'>
                        <ellipse cx='60' cy='30' rx='28' ry='11' fill='#90caf9'/>
                        <ellipse cx='60' cy='30' rx='20' ry='8' fill='#e3f2fd'/>
                        <text x='50%' y='50%' text-anchor='middle' fill='#0d47a1' font-size='14' font-family='Arial' dy='.3em'>ETSI</text>
                    </svg>
                </div>
                <h2 style='color:#1976d2;margin-bottom:1.2rem;'>ETSI Watchdog Login / Signup</h2>
                <div style='margin-bottom:1.2rem;'>
                    <span style='color:#0d47a1;font-size:0.95rem;'>Welcome! Please log in or sign up to use the dashboard.</span>
                </div>
                <div style='margin-bottom:1.2rem;'>
                    <span style='font-size:1.1rem;color:#1976d2;'>Select Option:</span>
                    <div style='margin-top:0.7rem;'>
                        <!-- The radio will be rendered here -->
                    </div>
                </div>
            """, unsafe_allow_html=True)
            auth_choice = st.radio("", ["Login", "Sign Up"], horizontal=True)
            if auth_choice == "Login":
                login_form()
            else:
                signup_form()
            st.markdown("</div>", unsafe_allow_html=True)
            st.stop()

# --- Main Dashboard ---
st.markdown("""
<div class='glass-card'>
<h1 style='color:#1976d2;'>ETSI Watchdog Drift Detection Dashboard</h1>
<p>Welcome to the ETSI Watchdog dashboard! Upload your reference and current CSV files, select features, and run drift detection.</p>
<ul>
<li>Upload two CSV files with matching columns (e.g., <code>age</code>, <code>salary</code>).</li>
<li>Select features to analyze.</li>
<li>Click <b>Run Drift Detection</b> to see results.</li>
</ul>
</div>
""", unsafe_allow_html=True)

with st.expander("Download Example CSVs"):
    example_ref = "age,salary\n25,50000\n30,60000\n35,70000\n40,80000"
    example_live = "age,salary\n26,52000\n31,61000\n36,72000\n41,83000"
    st.download_button("Download reference.csv", example_ref, "reference.csv", key="download_ref")
    st.download_button("Download current.csv", example_live, "current.csv", key="download_live")



# --- About ETSI Watchdog Sidebar (Clickable Modal) ---
import streamlit.components.v1 as components
with st.sidebar:
    st.markdown("<h3 style='color:#1976d2;cursor:pointer;' id='about-link'>About ETSI Watchdog</h3>", unsafe_allow_html=True)
    show_about = st.button("Learn More About Us", key="about_btn")
    if show_about:
        about_html = '''
        <div style="font-size:15px; background:#bbdefb; padding:16px; border-radius:10px; box-shadow:0 0 10px #90caf9;">
        <b>Purpose:</b> Automated drift detection for ML data streams.<br>
        <b>Roadmap & Vision:</b>
        <ul>
        <li><b>Multi-Algorithm Support:</b> JSD, Wasserstein, K-S, DeepDrift</li>
        <li><b>Plug-in Architecture:</b> Custom drift metrics via API</li>
        <li><b>Real-Time Stream Hooks:</b> Kafka/Redis/WebSockets</li>
        <li><b>Concept Drift Detection:</b> Model integration for label/concept drift</li>
        <li><b>CLI & YAML Configs:</b> Full CLI + YAML pipeline support</li>
        <li><b>Benchmark Suite:</b> Synthetic dataset benchmarking</li>
        <li><b>Dashboard UI:</b> Visual drift monitoring</li>
        <li><b>Drift Summary Reports:</b> PDF/HTML with histograms, timestamps</li>
        <li><b>Sklearn & Pandas Integration:</b> .fit()/.transform() support</li>
        </ul>
        <b>CLI Example:</b><br>
        <code>etsi-watchdog detect --ref ref.csv --live live.csv --features age salary</code>
        <br><br>
        <b>Contact:</b> Kavlin-Kaur | <a href='mailto:your@email.com'>your@email.com</a>
        </div>
        '''
        st.markdown(about_html, unsafe_allow_html=True)
    st.markdown("---")
    st.caption("ETS-Watchdog v3 | Roadmap & Vision | Made by Kavlin-Kaur")

st.sidebar.header("Upload Data")
ref_file = st.sidebar.file_uploader("Reference CSV", type=["csv"])
live_file = st.sidebar.file_uploader("Current CSV", type=["csv"])
# --- Main Dashboard ---
st.title("ETSI Watchdog Drift Detection Dashboard")
st.markdown("""
Welcome to the ETSI Watchdog dashboard! Upload your reference and current CSV files, select features, and run drift detection. 

**Instructions:**
- Upload two CSV files with matching columns (e.g., `age`, `salary`).
- Select features to analyze.
- Click **Run Drift Detection** to see results.
""")

with st.expander("Download Example CSVs"):
    example_ref = "age,salary\n25,50000\n30,60000\n35,70000\n40,80000"
    example_live = "age,salary\n26,52000\n31,61000\n36,72000\n41,83000"
    st.download_button("Download reference.csv", example_ref, "reference.csv")
    st.download_button("Download current.csv", example_live, "current.csv")

# --- Python Code Tester Section ---
st.markdown("---")
st.markdown("<h3 style='color:#1976d2;'>Test Python Code</h3>", unsafe_allow_html=True)
code_input = st.text_area("Enter Python code to test:", "print('Hello, ETSI!')", height=100)
if st.button("Run Code"):
    import io
    import sys
    old_stdout = sys.stdout
    sys.stdout = mystdout = io.StringIO()
    try:
        exec(code_input, {})
        output = mystdout.getvalue()
        st.success("Code executed successfully!")
        st.text_area("Output:", output, height=100)
    except Exception as e:
        st.error(f"Error: {e}")
    finally:
        sys.stdout = old_stdout

if ref_file and live_file:
    ref = pd.read_csv(ref_file)
    live = pd.read_csv(live_file)
    st.write("### Reference Data (first 5 rows)", ref.head())
    st.write("### Current Data (first 5 rows)", live.head())
    st.write(f"Reference shape: {ref.shape}, Columns: {list(ref.columns)}")
    st.write(f"Current shape: {live.shape}, Columns: {list(live.columns)}")
    st.write(f"Missing values in Reference: {ref.isnull().sum().sum()}")
    st.write(f"Missing values in Current: {live.isnull().sum().sum()}")
    features = st.multiselect("Select features for drift detection", ref.columns.tolist(), help="Choose columns present in both files.")
    if st.button("Run Drift Detection"):
        if not features:
            st.warning("Please select at least one feature.")
        else:
            try:
                check = DriftCheck(ref)
                results = check.run(live, features=features)
                st.success("Drift detection completed!")
                for feat, result in results.items():
                    st.subheader(f"Feature: {feat}")
                    st.text(result.summary())
                    st.pyplot(result.plot())
            except Exception as e:
                st.error(f"Error running drift detection: {e}")
else:
    st.info("Please upload both reference and current CSV files.")

st.markdown("---")
st.caption("Created by et-si.ai contributed by Kavlin Kaur| Powered by etsi-watchdog & Streamlit")
