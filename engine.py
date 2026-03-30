import os
import io
import json
import re
import streamlit as st
from pathlib import Path
from PyPDF2 import PdfReader
from dotenv import load_dotenv, find_dotenv
from O365 import Account
from langchain_groq import ChatGroq

load_dotenv(find_dotenv())

def get_secret(secret_name):
    """Fetches a secret from Streamlit Cloud or local environment variables."""
    try:
        # Check if we are running in Streamlit Cloud and the secret exists
        if secret_name in st.secrets:
            return st.secrets[secret_name]
    except Exception:
        # If st.secrets throws an error (e.g., running locally without .streamlit/secrets.toml)
        pass 
    
    # Fall back to local .env file
    return os.getenv(secret_name)


class ResumeEngine:
    def __init__(self):
        # 1. Initialize Groq AI
        groq_key = get_secret("GROQ_API_KEY")
        if not groq_key:
            raise ValueError("GROQ_API_KEY is missing in the .env file or Streamlit secrets.")
        
        self.llm = ChatGroq(
            model="llama-3.1-8b-instant",
            api_key=groq_key,
            temperature=0
        )

        # 2. Initialize O365 Service Principal (Client Secret)
        # Headless flow requires Application Permissions in Azure
        self.client_id = get_secret("O365_CLIENT_ID")
        self.client_secret = get_secret("O365_CLIENT_SECRET")
        self.tenant_id = get_secret("O365_TENANT_ID")
        
        if not all([self.client_id, self.client_secret, self.tenant_id]):
            raise ValueError("O365 credentials (ID, Secret, or Tenant) are missing in .env or secrets.")

        self.credentials = (self.client_id, self.client_secret)
        
        # auth_flow_type='credentials' is for Service Principal access
        self.account = Account(
            self.credentials, 
            auth_flow_type='credentials', 
            tenant_id=self.tenant_id
        )

    def _clean_json_output(self, text):
        """Sanitizes LLM output by removing markdown and extra text."""
        text = re.sub(r'```json|```', '', text).strip()
        # Find the first { and last } to isolate the JSON object
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return match.group(0)
        return text

    def get_authenticated_account(self):
        """Authenticates using the App's Client Secret and ensures token is loaded."""
        if self.account.authenticate():
            return self.account
        else:
            raise Exception("Service Principal Authentication Failed. Please check Azure Client Secret and Tenant ID.")

    def check_auth_status(self):
        """Used by app.py to show connection status."""
        try:
            return self.account.authenticate()
        except:
            return False

    def extract_text_from_bytes(self, pdf_bytes):
        """Parses PDF binary streams into text."""
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
        prompt = f"""
        Identify the primary programming language or skill category for this JD (e.g., Java, Python, PHP, React). 
        Return ONLY the single word representing the category. Do not include punctuation or sentences.
        
        JD Content:
        {jd_text[:1000]}
        """
        try:
            res = self.llm.invoke(prompt)
            # Remove any non-alphanumeric characters and convert to lowercase
            category = re.sub(r'[^a-zA-Z0-9]', '', res.content.strip().lower())
            return category
        except:
            return None

    def load_resumes_from_onedrive(self, root_folder="Resumes", target_category=None):
        """
        Optimized: If target_category is provided, it searches for a subfolder 
        named after that category inside the root_folder.
        """
        account = self.get_authenticated_account()
        
        # Updated to use get_secret()
        user_email = get_secret("O365_USER_EMAIL")
        
        if not user_email:
            raise Exception("O365_USER_EMAIL is missing in .env or secrets.")

        storage = account.storage(resource=user_email)
        drive = storage.get_default_drive()

        # 1. Find the Root "Resumes" Folder
        print(f"Searching for root folder '{root_folder}'...")
        search_res = drive.search(root_folder)
        parent_folder = next((item for item in search_res if item.is_folder and item.name.lower() == root_folder.lower()), None)
        
        if not parent_folder:
            # Fallback: Manual root scan if search index is slow
            root_items = drive.get_root_folder().get_items()
            parent_folder = next((item for item in root_items if item.is_folder and item.name.lower() == root_folder.lower()), None)

        if not parent_folder:
            raise Exception(f"Root folder '{root_folder}' not found.")

        # 2. Optimization: If a category is detected, look for that specific subfolder
        final_target_folder = parent_folder
        if target_category:
            print(f"🎯 Optimization: Searching for subfolder '{target_category}'...")
            sub_items = parent_folder.get_items()
            for item in sub_items:
                if item.is_folder and item.name.lower() == target_category.lower():
                    final_target_folder = item
                    print(f"✅ Found targeted subfolder: {item.name}")
                    break
        
        # 3. Fetch Documents
        documents = []
        for item in final_target_folder.get_items():
            if not item.is_file or not item.name.lower().endswith(('.pdf', '.txt')):
                continue
            
            try:
                # Direct API Call for content retrieval
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
                else:
                    print(f"⚠️ Failed to download {item.name}: Status {response.status_code}")
            except Exception as e:
                print(f"❌ Skipping {item.name}: {e}")
        
        return documents

    def get_match_analysis(self, jd_text, resume_text):
        """Sends the JD and Resume to Groq AI for scoring and analysis."""
        prompt = f"""
        Act as a Technical Recruiter. Compare the Resume with the Job Description (JD).
        Return raw JSON ONLY.
        {{ "candidate_name": "Name", "email": "Email", "phone": "Phone", "matched_skills": [], "missing_skills": [], "match_percentage": 0, "summary": "" }}
        
        JD: {jd_text[:1500]}
        RESUME: {resume_text[:4000]}
        """
        try:
            res = self.llm.invoke(prompt)
            clean_json = self._clean_json_output(res.content)
            # Handle cases where LLM might return text around the JSON
            json_match = re.search(r'\{.*\}', clean_json, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
            return {"candidate_name": "Error", "match_percentage": 0}
        except Exception as e:
            print(f"AI Analysis error: {e}")
            return {"candidate_name": "Error", "match_percentage": 0}