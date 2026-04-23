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
    """AI determines the primary skill category to target specific subfolders."""
    # We use a more forceful 'System' style prompt for Gemini
    prompt = f"""
    You are a classification tool. Analyze the Job Description below.
    Identify the main technology or programming language (e.g., Java, Python, React, PHP).
    
    RULES:
    - Output ONLY the single word.
    - No punctuation, no sentences, no explanations.
    - If unsure, output 'general'.

    JD Content:
    {jd_text[:1500]}
    """
    try:
        # Using a list of messages is more stable for Gemini
        from langchain_core.messages import HumanMessage
        res = self.llm.invoke([HumanMessage(content=prompt)])
        
        # Aggressive cleaning: remove all non-alphanumeric and strip whitespace
        category = re.sub(r'[^a-zA-Z0-9]', '', res.content.strip().lower())
        
        if not category:
            return None
            
        print(f"🔍 AI detected category: {category}")
        return category
    except Exception as e:
        print(f"❌ Category detection failed: {e}")
        return None

    def load_resumes_from_onedrive(self, root_folder="Resumes", target_category=None):
        account = self.get_authenticated_account()
        user_email = get_secret("O365_USER_EMAIL")
        
        if not user_email:
            raise Exception("O365_USER_EMAIL is missing.")

        storage = account.storage(resource=user_email)
        drive = storage.get_default_drive()

        search_res = drive.search(root_folder)
        parent_folder = next((item for item in search_res if item.is_folder and item.name.lower() == root_folder.lower()), None)
        
        if not parent_folder:
            root_items = drive.get_root_folder().get_items()
            parent_folder = next((item for item in root_items if item.is_folder and item.name.lower() == root_folder.lower()), None)

        if not parent_folder:
            raise Exception(f"Root folder '{root_folder}' not found.")

        final_target_folder = parent_folder
        if target_category:
            sub_items = parent_folder.get_items()
            for item in sub_items:
                if item.is_folder and item.name.lower() == target_category.lower():
                    final_target_folder = item
                    break
        
        documents = []
        for item in final_target_folder.get_items():
            if not item.is_file or not item.name.lower().endswith(('.pdf', '.txt')):
                continue
            
            try:
                url = f"https://graph.microsoft.com/v1.0/users/{user_email}/drive/items/{item.object_id}/content"
                response = item.con.get(url)
                
                if response.status_code == 200:
                    file_content = response.content
                    text = ""
                    if item.name.lower().endswith('.pdf'):
                        text = self.extract_text_from_bytes(file_content)
                    else:
                        text = file_content.decode('utf-8', errors='ignore')
                    
                    if text:
                        documents.append({
                            "page_content": text,
                            "metadata": {
                                "filename": item.name, 
                                "web_url": item.web_url, 
                                "id": item.object_id
                            }
                        })
            except Exception as e:
                print(f"❌ Skipping {item.name}: {e}")
        
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