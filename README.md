<div align="center">
  <h1>🚀 AI Resume Screening Assistant</h1>
  <p><b>An intelligent, cloud-connected recruitment tool powered by Llama-3 & Microsoft 365.</b></p>

  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/Python-3.9+-blue.svg?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  </a>
  <a href="https://streamlit.io/">
    <img src="https://img.shields.io/badge/Streamlit-FF4B4B.svg?style=for-the-badge&logo=Streamlit&logoColor=white" alt="Streamlit">
  </a>
  <a href="https://groq.com/">
    <img src="https://img.shields.io/badge/Groq_API-F55036.svg?style=for-the-badge&logo=Groq&logoColor=white" alt="Groq">
  </a>
  <a href="https://www.langchain.com/">
    <img src="https://img.shields.io/badge/LangChain-1C3C3C.svg?style=for-the-badge&logo=LangChain&logoColor=white" alt="LangChain">
  </a>
  <a href="https://azure.microsoft.com/">
    <img src="https://img.shields.io/badge/Azure_AD-0078D4.svg?style=for-the-badge&logo=microsoft-azure&logoColor=white" alt="Azure">
  </a>
</div>

<br/>

> **Overview:** This application securely connects to your organization's Microsoft 365 (OneDrive/SharePoint) environment, scans folders for candidate resumes, and uses ultra-fast AI inference (Groq) to automatically score candidates against a specific Job Description.

---

## 📑 Table of Contents
- [✨ Key Features](#-key-features)
- [🏗️ System Architecture](#️-system-architecture)
- [📋 Prerequisites](#-prerequisites)
- [🚀 Quick Start (Local)](#-quick-start-local)
- [☁️ Cloud Deployment](#️-cloud-deployment)
- [📂 Project Structure](#-project-structure)

---

## ✨ Key Features

* **🔒 Enterprise-Grade Security:** Utilizes Microsoft Authentication Library (MSAL) for secure user login via Streamlit or background Service Principal access.
* **☁️ Direct Cloud Integration:** Reads resumes directly from OneDrive/SharePoint. No manual downloading or local file handling required!
* **🧠 Lightning-Fast AI:** Powered by LangChain and Groq's `Llama-3.1-8b` model for instant, intelligent JSON-formatted candidate scoring.
* **📄 Smart Parsing:** On-the-fly binary extraction of PDFs using `PyPDF2`.
* **🌐 Cloud-Ready:** Pre-configured to deploy seamlessly on Streamlit Community Cloud with robust Secrets Management.

---

## 🏗️ System Architecture

1. **Frontend:** Streamlit provides a clean, interactive web interface.
2. **Auth Layer:** `auth.py` handles the Microsoft Device Flow, storing secure tokens in `st.session_state`.
3. **Engine:** `engine.py` orchestrates the backend:
   * Connects to O365 via Python `O365` library.
   * Downloads file bytes into memory.
   * Parses text and prompts the Groq LLM.
4. **Output:** The LLM returns structured JSON (Matched Skills, Missing Skills, Match %) displayed beautifully on the UI.

---


