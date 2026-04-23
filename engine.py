import os
import io
import json
import re
import time
import streamlit as st
from pathlib import Path
from PyPDF2 import PdfReader
from dotenv import load_dotenv, find_dotenv
from O365 import Account
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

# Load environment variables
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
        # 1. Initialize Gemini 2.0 Flash
        google_key = get_secret("GOOGLE_API_KEY")
        if not google_key:
            raise ValueError("GOOGLE_API_KEY is missing in secrets/env.")
        
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash", 
            google_api_key=google_key,
            temperature=0
        )

        # 2. Initialize O365 Service Principal (Client Secret Flow)
        self.client_id = get_secret("O365_CLIENT_ID")
        self.client_secret = get_secret("O365_CLIENT_SECRET")
        self.tenant_id = get_secret("O365_TENANT_ID")
        
        if not all([self.client_id, self.client_secret, self.tenant_id]):
            raise ValueError("O365 credentials missing (ID, Secret, or Tenant).")

        self.credentials = (self.client_id, self.client_secret)
        self.account = Account(
            self.credentials, 
            auth_flow_type='credentials', 
            tenant_id=self.tenant_id
        )

    def _clean_json_output(self, text):
        """Sanitizes LLM output by extracting the JSON object."""
        text = re.sub(r'```json|```', '', text).strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return match.group(0)
        return text

    def get_authenticated_account(self):
        """Authenticates with Microsoft Graph API."""
        if self.account.authenticate():
            return self.account
        else:
            raise Exception("O365 Authentication Failed. Verify Azure App Permissions.")

    def check_auth_status(self):
        """Helper for UI to check connection."""
        try:
            return self.account.authenticate()
        except:
            return False

    def extract_text_from_bytes(self, pdf_bytes):
        """Extracts text from PDF binary content."""
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages:
                t = page.extract_text()
                if t: text += t + "\n"
            return text.strip()
        except Exception as e:
            st.error(f"Error parsing PDF: {e}")
            return ""

    def get_jd_category(self, jd_text):
        """Identifies the skill category for folder targeting with Rate Limit handling."""
        if not jd_text or len(jd_text.strip()) < 10:
            return None

        prompt = f"""
        Identify the most important technical tool or platform in this Job Description.
        OUTPUT ONLY THE SINGLE WORD (e.g., Matillion, Snowflake, Java).
        
        JD: {jd_text[:1500]}
        """
        try:
            # Small delay to respect Free Tier RPM limits
            time.sleep(2) 
            res = self.llm.invoke([HumanMessage(content=prompt)])
            category = res.content.strip().lower()
            category = re.sub(r'[^a-z0-9]', '', category)
            
            if category:
                st.info(f"🔍 AI detected primary skill: **{category.capitalize()}**")
                return category
        except Exception as e:
            if "429" in str(e):
                st.warning("Rate limit hit during category detection. Waiting 15s...")
                time.sleep(15)
                return self.get_jd_category(jd_text)
            st.error(f"Category extraction error: {e}")
        return None

    def load_resumes_from_onedrive(self, root_folder_name="Resumes", target_category=None):
        """Directly traverses OneDrive folders to find resumes."""
        account = self.get_authenticated_account()
        user_email = get_secret("O365_USER_EMAIL")
        storage = account.storage(resource=user_email)
        drive = storage.get_default_drive()

        # 1. Manually find the Root Folder (Direct traversal is more reliable)
        root_items = drive.get_root_folder().get_items()
        parent_folder = next((item for item in root_items if item.is_folder and item.name.lower() == root_folder_name.lower()), None)
    
        if not parent_folder:
            st.error(f"❌ Root folder '{root_folder_name}' not found.")
            return []

        # 2. Look for Skill Subfolder
        final_target_folder = parent_folder
        if target_category:
            sub_items = parent_folder.get_items()
            for item in sub_items:
                if item.is_folder and target_category.lower() in item.name.lower():
                    final_target_folder = item
                    st.success(f"📂 Entering Folder: **{item.name}**")
                    break
            
            if final_target_folder == parent_folder:
                st.warning(f"⚠️ No folder found for '{target_category}'. Searching root.")

        # 3. Process Files
        documents = []
        files = final_target_folder.get_items() 
    
        for file in files:
            if file.is_file and file.name.lower().endswith(('.pdf', '.txt')):
                try:
                    content = file.get_content()
                    text = ""
                    if file.name.lower().endswith('.pdf'):
                        text = self.extract_text_from_bytes(content)
                    else:
                        text = content.decode('utf-8', errors='ignore')
                    
                    if text.strip():
                        documents.append({
                            "page_content": text,
                            "metadata": {"filename": file.name, "url": file.web_url}
                        })
                except Exception as e:
                    print(f"Error reading {file.name}: {e}")

        if not documents:
            st.warning(f"No valid resumes found in '{final_target_folder.name}'.")
        return documents

    def get_match_analysis(self, jd_text, resume_text):
        """Sends JD and Resume to Gemini for analysis with Rate Limit Protection."""
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
            # --- CRITICAL: Wait 4 seconds between each resume analysis ---
            # This ensures we don't exceed the 15 Requests Per Minute limit.
            time.sleep(4) 
            
            res = self.llm.invoke([HumanMessage(content=prompt)])
            clean_json = self._clean_json_output(res.content)
            return json.loads(clean_json)
        except Exception as e:
            if "429" in str(e):
                st.warning(f"Rate limit exceeded. Pausing for 30 seconds to refresh quota...")
                time.sleep(30)
                return self.get_match_analysis(jd_text, resume_text)
            
            st.error(f"Analysis Error for a resume: {e}")
            return {"candidate_name": "Error", "match_percentage": 0}