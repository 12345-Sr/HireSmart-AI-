import os
import io
import json
import re
import streamlit as st
from pathlib import Path
from PyPDF2 import PdfReader
from dotenv import load_dotenv, find_dotenv
from O365 import Account
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

load_dotenv(find_dotenv())

def get_secret(secret_name):
    """Fetches a secret from Streamlit Cloud or local environment variables."""
    try:
        if secret_name in st.secrets:
            return st.secrets[secret_name]
    except Exception:
        pass 
    return os.getenv(secret_name)

class ResumeEngine:
    def __init__(self):
        # 1. Initialize Gemini AI
        google_key = get_secret("GOOGLE_API_KEY")
        if not google_key:
            raise ValueError("GOOGLE_API_KEY is missing.")
        
        self.llm = ChatGoogleGenerativeAI(
            model="models/gemini-1.5-flash", 
            google_api_key=google_key,
            temperature=0
        )

        # 2. Initialize O365 Service Principal
        self.client_id = get_secret("O365_CLIENT_ID")
        self.client_secret = get_secret("O365_CLIENT_SECRET")
        self.tenant_id = get_secret("O365_TENANT_ID")
        
        if not all([self.client_id, self.client_secret, self.tenant_id]):
            raise ValueError("O365 credentials missing.")

        self.credentials = (self.client_id, self.client_secret)
        self.account = Account(
            self.credentials, 
            auth_flow_type='credentials', 
            tenant_id=self.tenant_id
        )

    def _clean_json_output(self, text):
        """Sanitizes LLM output by removing markdown and extra text."""
        text = re.sub(r'```json|```', '', text).strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return match.group(0)
        return text

    def get_authenticated_account(self):
        if self.account.authenticate():
            return self.account
        else:
            raise Exception("Service Principal Authentication Failed. Check Azure Client Secret and Tenant ID.")

    def check_auth_status(self):
        try:
            return self.account.authenticate()
        except:
            return False

    def extract_text_from_bytes(self, pdf_bytes):
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages:
                t = page.extract_text()
                if t: text += t + "\n"
            return text.strip()
        except Exception as e:
            st.error(f"PDF Extraction error: {e}")
            return ""

    def get_jd_category(self, jd_text):
        """Identifies the core technology/tool for folder matching."""
        prompt = f"""
        Analyze this Job Description and identify the single most important technical tool (e.g., Matillion, Snowflake, Java).
        Output ONLY the single word. No sentences or punctuation.
        
        JD: {jd_text[:1500]}
        """
        try:
            # Using HumanMessage ensures the prompt is handled correctly by Gemini
            res = self.llm.invoke([HumanMessage(content=prompt)])
            category = res.content.strip().lower()
            category = re.sub(r'[^a-z0-9]', '', category) # Clean string for folder matching
            
            if category:
                st.write(f"🔍 AI detected target skill folder: **{category}**")
                return category
            return None
        except Exception as e:
            st.error(f"❌ Skill Extraction Error: {e}")
            return None

    def load_resumes_from_onedrive(self, root_folder_name="Resumes", target_category=None):
        """Directly traverses OneDrive to find resumes in specific folders."""
        account = self.get_authenticated_account()
        user_email = get_secret("O365_USER_EMAIL")
        storage = account.storage(resource=user_email)
        drive = storage.get_default_drive()

        # 1. Find Root Folder via iteration (More reliable than .search())
        root_items = drive.get_root_folder().get_items()
        parent_folder = next((item for item in root_items if item.is_folder and item.name.lower() == root_folder_name.lower()), None)
    
        if not parent_folder:
            st.error(f"❌ Folder '{root_folder_name}' not found in OneDrive root.")
            return []

        # 2. Optimization: Skill-specific folder jumping
        final_target_folder = parent_folder
        if target_category:
            sub_items = parent_folder.get_items()
            for item in sub_items:
                if item.is_folder and target_category.lower() in item.name.lower():
                    final_target_folder = item
                    st.info(f"📂 Accessing Subfolder: **{item.name}**")
                    break
    
        # 3. Fetch and Parse Files
        documents = []
        items = final_target_folder.get_items() 
    
        for item in items:
            # Check for PDF or TXT files
            if item.is_file and item.name.lower().endswith(('.pdf', '.txt')):
                try:
                    content = item.get_content() 
                    
                    if item.name.lower().endswith('.pdf'):
                        text = self.extract_text_from_bytes(content)
                    else:
                        text = content.decode('utf-8', errors='ignore')
                    
                    if text.strip():
                        documents.append({
                            "page_content": text,
                            "metadata": {"filename": item.name, "url": item.web_url}
                        })
                except Exception as e:
                    st.warning(f"Could not read {item.name}: {e}")

        if not documents:
            st.warning(f"No resumes found in path: {final_target_folder.name}")
        else:
            st.success(f"✅ Loaded {len(documents)} resumes from {final_target_folder.name}")

        return documents

    def get_match_analysis(self, jd_text, resume_text):
        """Analyzes JD vs Resume using Gemini."""
        prompt = f"""
        Act as a Technical Recruiter. Compare the Resume with the Job Description (JD).
        Return raw JSON ONLY.
        
        {{ 
          "candidate_name": "Name", 
          "email": "Email", 
          "phone": "Phone", 
          "matched_skills": [], 
          "missing_skills": [], 
          "match_percentage": 0, 
          "summary": "" 
        }}
        
        JD: {jd_text}
        RESUME: {resume_text}
        """
        try:
            res = self.llm.invoke([HumanMessage(content=prompt)])
            clean_json = self._clean_json_output(res.content)
            return json.loads(clean_json)
        except Exception as e:
            st.error(f"Gemini Analysis error: {e}")
            return {"candidate_name": "Error", "match_percentage": 0}