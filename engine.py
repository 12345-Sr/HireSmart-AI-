import os
import io
import json
import re
import streamlit as st
from pathlib import Path
from PyPDF2 import PdfReader
from dotenv import load_dotenv, find_dotenv
from O365 import Account
# --- CHANGED: Import Google Generative AI instead of Groq ---
from langchain_google_genai import ChatGoogleGenerativeAI

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
        # --- CHANGED: Updated to use GOOGLE_API_KEY and Gemini Model ---
        google_key = get_secret("GOOGLE_API_KEY")
        if not google_key:
            raise ValueError("GOOGLE_API_KEY is missing in the .env file or Streamlit secrets.")
        
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",  # Best for speed and parsing efficiency
            google_api_key=google_key,
            temperature=0,
            convert_system_message_to_human=True # Ensures compatibility with Gemini's API
        )

        # 2. Initialize O365 Service Principal
        self.client_id = get_secret("O365_CLIENT_ID")
        self.client_secret = get_secret("O365_CLIENT_SECRET")
        self.tenant_id = get_secret("O365_TENANT_ID")
        
        if not all([self.client_id, self.client_secret, self.tenant_id]):
            raise ValueError("O365 credentials missing in .env or secrets.")

        self.credentials = (self.client_id, self.client_secret)
        self.account = Account(
            self.credentials, 
            auth_flow_type='credentials', 
            tenant_id=self.tenant_id
        )

    def _clean_json_output(self, text):
        """Sanitizes LLM output by removing markdown and extra text."""
        # Gemini often wraps JSON in markdown blocks
        text = re.sub(r'```json|```', '', text).strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return match.group(0)
        return text

    def get_authenticated_account(self):
        if self.account.authenticate():
            return self.account
        else:
            raise Exception("Service Principal Authentication Failed.")

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
            print(f"PDF Extraction error: {e}")
            return ""

    def get_jd_category(self, jd_text):
        """
        Identifies the core technology/tool from a JD even without explicit 'Primary' labels.
        """
        prompt = f"""
        Analyze this Job Description and identify the single most important technical tool or platform required.
    
        RULES:
        1. If a 'Primary Skill' is listed, pick that.
        2. Otherwise, pick the most specialized technical tool mentioned (e.g., Snowflake, Matillion, AWS, Salesforce).
        3. Ignore generic terms like 'Communication', 'Remote', or 'Full-time'.
        4. Output ONLY the single word (the tool name). No sentences.
    
        JOB DESCRIPTION:
        {jd_text[:2000]}
        """
        try:
            from langchain_core.messages import HumanMessage
            # Use Gemini to extract the core tool
            res = self.llm.invoke([HumanMessage(content=prompt)])
        
            # Clean the output string
            category = res.content.strip().lower()
            # Remove any non-alphanumeric characters
            category = re.sub(r'[^a-z0-9]', '', category)
        
            # Safety check: if the AI returns too many words, it failed
            if len(category.split()) > 1:
                return None

            print(f"✅ Target Folder Identified: {category}")
            return category
        
        except Exception as e:
            print(f"❌ Extraction Error: {e}")
            return None

    def load_resumes_from_onedrive(self, root_folder="Resumes", target_category=None):
        account = self.get_authenticated_account()
        user_email = get_secret("O365_USER_EMAIL")
        storage = account.storage(resource=user_email)
        drive = storage.get_default_drive()

        # 1. Locate Root Folder
        search_res = drive.search(root_folder)
        parent_folder = next((item for item in search_res if item.is_folder and item.name.lower() == root_folder.lower()), None)
    
        if not parent_folder:
            raise Exception(f"Folder '{root_folder}' not found in OneDrive.")

        # 2. Determine Search Folder
        final_target_folder = parent_folder

        if target_category:
            print(f"🎯 Searching for subfolder matching: {target_category}")
            sub_items = parent_folder.get_items() # Fetch subfolders

            for item in sub_items:
                if item.is_folder and target_category.lower() in item.name.lower():
                    final_target_folder = item
                    print(f"✅ Target found: {item.name}")
                    break
    
        # 3. CRITICAL FIX: Ensure we are actually getting items
        documents = []
        # Use a limit if the folder is massive to prevent timeouts
        items = final_target_folder.get_items() 
    
        item_count = 0
        for item in items:
            if item.is_file and item.name.lower().endswith(('.pdf', '.txt')):
                item_count += 1
            try:
                # Use the content property directly if possible, or the download URL
                content = item.get_content() 
                
                if item.name.lower().endswith('.pdf'):
                    text = self.extract_text_from_bytes(content)
                else:
                    text = content.decode('utf-8', errors='ignore')
                
                if text:
                    documents.append({
                        "page_content": text,
                        "metadata": {"filename": item.name, "url": item.web_url}
                    })
            except Exception as e:
                print(f"Could not read {item.name}: {e}")

        print(f"📂 Found {len(documents)} valid resumes in '{final_target_folder.name}'")
        return documents

    def get_match_analysis(self, jd_text, resume_text):
        """Sends the JD and Resume to Gemini for scoring and analysis."""
        # Note: Gemini has a massive context window (1M+ tokens), 
        # so we can safely send more text than Groq could.
        prompt = f"""
        Act as a Technical Recruiter. Compare the Resume with the Job Description (JD).
        Return raw JSON ONLY. Do not include any conversational text.
        
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
            res = self.llm.invoke(prompt)
            clean_json = self._clean_json_output(res.content)
            return json.loads(clean_json)
        except Exception as e:
            print(f"Gemini AI Analysis error: {e}")
            return {"candidate_name": "Error", "match_percentage": 0}