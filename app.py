import streamlit as st
import auth
import pandas as pd
import io
import asyncio
from engine import ResumeEngine
from PyPDF2 import PdfReader
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Font

st.set_page_config(page_title="My AI Assistant")
st.title("My AI Assistant")

# 1. THE GATEKEEPER
# If the user is not logged in, require_login() shows the UI.
# st.stop() prevents the rest of the code from running until they finish logging in.
if not auth.require_login():
    st.stop() 

# 2. MAIN APP CODE (Runs only if logged in)
st.success("You are securely logged in!")

# Put a logout button neatly in the sidebar
if st.sidebar.button("Log Out"):
    auth.logout()

st.write("---")
st.write("### Welcome to your App!")
#st.write("Your secure access token is stored in memory and ready to use.")

# You can access the token anywhere in this file like this:
access_token = st.session_state["access_token"]

# (Your LangChain, Pandas, and PDF logic will go here)

# --- THREADING FIX FOR STREAMLIT ---
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

st.set_page_config(page_title="HireSmart AI", layout="wide", page_icon="👔")

# --- Custom Styling ---
st.markdown("""
    <style>
    .main-header { font-size: 2.5rem; font-weight: 700; color: #1E3A8A; margin-bottom: 0.5rem; }
    .sub-text { color: #6B7280; margin-bottom: 2rem; }
    .section-header { font-size: 1.5rem; font-weight: 600; color: #1F2937; border-bottom: 2px solid #E5E7EB; padding-bottom: 0.5rem; margin-bottom: 1rem; }
    .stDataFrame { border: 1px solid #E5E7EB; border-radius: 8px; }
    .status-box { padding: 10px; border-radius: 5px; margin-bottom: 10px; }
    </style>
    """, unsafe_allow_html=True)

st.markdown('<div class="main-header">HireSmart AI: Smart Resume Ranker</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-text">Intelligent candidate shortlisting powered by AI</div>', unsafe_allow_html=True)

# --- Helper Functions ---
def generate_excel_report(results):
    """Formats and exports the results to match the requested structure with clickable links."""
    df = pd.DataFrame(results)
    
    if not df.empty:
        for col in ['matched_skills', 'missing_skills']:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: ", ".join(x) if isinstance(x, list) else x)
    
    column_order = [
        "candidate_name", "match_percentage", "email", "phone", 
        "resume_link", "matched_skills", "missing_skills", "summary"
    ]
    
    rename_map = {
        "candidate_name": "Name",
        "match_percentage": "Score",
        "email": "Email",
        "phone": "Phone",
        "resume_link": "Resume Link",
        "matched_skills": "Matched Skills",
        "missing_skills": "Missing Skills",
        "summary": "Summary"
    }

    for col in column_order:
        if col not in df.columns:
            df[col] = "N/A"
            
    df = df[column_order].rename(columns=rename_map)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Shortlisted Candidates')
        workbook = writer.book
        worksheet = writer.sheets['Shortlisted Candidates']
        
        col_widths = {
            "Name": 25, 
            "Score": 10, 
            "Email": 30, 
            "Phone": 20, 
            "Resume Link": 40, 
            "Matched Skills": 60, 
            "Missing Skills": 60, 
            "Summary": 60
            }
        
        link_col_idx = None
        for i, col_name in enumerate(df.columns):
            if col_name == "Resume Link":
                link_col_idx = i + 1
            column_letter = get_column_letter(i + 1)
            worksheet.column_dimensions[column_letter].width = col_widths.get(col_name, 20)
            
            for cell in worksheet[column_letter]:
                cell.alignment = Alignment(wrap_text=True, vertical='top')
                if i + 1 == link_col_idx and cell.row > 1 and cell.value and str(cell.value).startswith("http"):
                    cell.hyperlink = cell.value
                    cell.font = Font(color="0563C1", underline="single")

    return output.getvalue()

@st.cache_resource
def get_engine():
    return ResumeEngine()

engine = get_engine()

# --- Sidebar Configuration ---
with st.sidebar:
    st.markdown('<div class="section-header">Configuration</div>', unsafe_allow_html=True)
    root_folder = st.text_input("Main OneDrive Folder", value="Resumes")
    use_smart_targeting = st.checkbox("Use Smart Subfolder Targeting", value=True, help="Detects primary skill (Java, Python, etc.) from JD and searches only that subfolder.")
    min_score = st.slider("Minimum Match Score (%)", 0, 100, 50)
    
    st.divider()
    
    if engine.check_auth_status():
        st.success("✅ OneDrive Connected")
    else:
        st.error("🔑 Authentication Required")
    
    if st.button("🔄 Reset Session", use_container_width=True):
        st.session_state.clear()
        st.rerun()

# --- Job Description Input ---
col1, col2 = st.columns([1, 1], gap="large")
jd_text = ""

with col1:
    st.markdown('<div class="section-header"> Job Description</div>', unsafe_allow_html=True)
    jd_tab1, jd_tab2 = st.tabs(["📄 Upload PDF", "📝 Paste Text"])
    
    with jd_tab1:
        uploaded_jd = st.file_uploader("Upload JD PDF", type="pdf", label_visibility="collapsed")
        if uploaded_jd:
            try:
                reader = PdfReader(uploaded_jd)
                jd_text = "\n".join([p.extract_text() for p in reader.pages if p.extract_text()])
            except Exception as e:
                st.error(f"Error reading PDF: {e}")
    
    with jd_tab2:
        manual_jd = st.text_area("Paste JD details here", placeholder="Include Role, Experience, Location, and Skills...", height=250)
        if manual_jd:
            jd_text = manual_jd

    if jd_text.strip():
        st.success("✅ Job Description Ready")

with col2:
    st.markdown('<div class="section-header"> Analysis </div>', unsafe_allow_html=True)
    
    category = None
    if jd_text.strip() and use_smart_targeting:
        with st.spinner("AI detecting skill category..."):
            category = engine.get_jd_category(jd_text)
            if category:
                st.info(f"🎯 **Targeted Subfolder:** `{root_folder}/{category.upper()}/`")
            else:
                st.warning("⚠️ Could not determine category. Will search entire root folder.")
    elif jd_text.strip():
        st.info(f"📁 **Searching entire root:** `{root_folder}/`")

    can_run = len(jd_text.strip()) > 0
    
    if st.button("🚀 Run Targeted Shortlisting", disabled=not can_run, type="primary", use_container_width=True):
        with st.spinner(f"Analyzing candidates in {category if category else root_folder}..."):
            try:
                # Use the new optimized engine method
                resumes = engine.load_resumes_from_onedrive(root_folder, target_category=category if use_smart_targeting else None)
                
                if not resumes:
                    st.warning(f"No resumes found in the targeted path.")
                else:
                    final_results = []
                    progress_bar = st.progress(0)
                    
                    for i, resume in enumerate(resumes):
                        data = engine.get_match_analysis(jd_text, resume["page_content"])
                        score = int(data.get("match_percentage", 0))
                        
                        if score >= min_score:
                            data['resume_link'] = resume["metadata"].get('web_url', '#')
                            data['filename'] = resume["metadata"].get('filename', 'Unknown')
                            final_results.append(data)
                        
                        progress_bar.progress((i + 1) / len(resumes))
                    
                    st.session_state['results'] = sorted(final_results, key=lambda x: x.get("match_percentage", 0), reverse=True)
                    st.success(f"Processing complete! Analyzed {len(resumes)} files.")
            except Exception as e:
                st.error(f"System Error: {e}")

# --- Results Section ---
st.divider()

if 'results' in st.session_state:
    res_list = st.session_state['results']
    if not res_list:
        st.warning("No candidates met the threshold.")
    else:
        st.markdown('<div class="section-header">Shortlisted Candidates</div>', unsafe_allow_html=True)
        
        df_display = pd.DataFrame(res_list)
        st.dataframe(
            df_display[['match_percentage', 'candidate_name', 'email', 'phone', 'filename']], 
            column_config={
                "match_percentage": st.column_config.ProgressColumn("Match", min_value=0, max_value=100, format="%d%%"),
                "candidate_name": "Name",
                "email": "Email",
                "phone": "Phone",
                "filename": "Source File"
            },
            hide_index=True, use_container_width=True
        )
        
        excel_data = generate_excel_report(res_list)
        st.download_button(
            label="📥 Download Excel Report", 
            data=excel_data, 
            file_name=f"Shortlisted_Candidates.xlsx", 
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
            
        for res in res_list:
            with st.expander(f"**{res.get('candidate_name', 'Unknown')}** — {res.get('match_percentage', 0)}% Match"):
                c1, c2 = st.columns([1, 2])
                with c1:
                    st.markdown(f"**Email:** {res.get('email', 'N/A')}")
                    st.markdown(f"**Phone:** {res.get('phone', 'N/A')}")
                    st.markdown(f"[🔗 Open Original Resume]({res.get('resume_link', '#')})")
                with c2:
                    st.markdown(f"**AI Summary:** {res.get('summary', 'No summary available.')}")
                    st.markdown(f"✅ **Matched Skills:** {', '.join(res.get('matched_skills', []))}")
                    st.markdown(f"❌ **Missing Skills:** {', '.join(res.get('missing_skills', []))}")
else:
    st.info("Input a Job Description to start the targeted scan.")