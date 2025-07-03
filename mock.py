import streamlit as st
import fitz
from gtts import gTTS
import tempfile
import os
import unicodedata
from fpdf import FPDF
import google.generativeai as genai
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import uuid
import streamlit.components.v1 as components
import speech_recognition as sr
import pygame
import time

# --- Configuration ---
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    st.error("❌ Google API Key not found! Set GOOGLE_API_KEY in your .env file")
    st.stop()

genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-2.0-flash-exp")
st.set_page_config(page_title="AI Interview Assistant", layout="wide")

try:
    pygame.mixer.init()
except Exception as e:
    st.warning(f"Audio playback may not work: {e}")

# --- Helper Functions ---
def clean_text(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")

def save_pdf(title, content, feedback=""):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.multi_cell(0, 10, title)
    pdf.ln()
    pdf.set_font("helvetica", style='B', size=12)
    pdf.cell(0, 10, "Transcript:", ln=True)
    pdf.set_font("helvetica", size=11)
    pdf.multi_cell(0, 10, clean_text(content))
    if feedback:
        pdf.ln()
        pdf.set_font("helvetica", style='B', size=12)
        pdf.cell(0, 10, "AI Feedback:", ln=True)
        pdf.set_font("helvetica", size=11)
        pdf.multi_cell(0, 10, clean_text(feedback))
    temp_path = os.path.join(tempfile.gettempdir(), f"report_{uuid.uuid4().hex}.pdf")
    pdf.output(temp_path)
    return temp_path

def extract_text_from_pdf(uploaded_file):
    uploaded_file.seek(0)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.read())
        tmp.flush()
        tmp_path = tmp.name
    doc = fitz.open(tmp_path)
    text = " ".join([page.get_text() for page in doc])
    doc.close()
    os.unlink(tmp_path)
    return text

def extract_jd_from_url(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    for script in soup(["script", "style"]): script.decompose()
    return "\n".join([line.strip() for line in soup.get_text().splitlines() if line.strip()])

def generate_ai_content(prompt):
    try:
        response = model.generate_content(prompt)
        # Access response.text safely, handling cases where it might be empty
        return response.text.strip() or "AI response unavailable."
    except Exception as e:
        return f"Error: {str(e)}"

def analyze_resume_for_context(resume_text, jd):
    prompt = f"""Analyze resume/JD to understand candidate for adaptive interview. Identify: skills, projects, gaps, behavioral aspects, cultural fit, role alignment. Resume: {resume_text[:2000]} JD: {jd[:1000]}"""
    return generate_ai_content(prompt)

def text_to_speech_and_play(text):
    """Convert text to speech and play it automatically"""
    try:
        clean_speech_text = text.replace("AI Interviewer:", "").strip()
        tts = gTTS(text=clean_speech_text, lang='en', slow=False)
        audio_path = os.path.join(tempfile.gettempdir(), f"question_{uuid.uuid4().hex}.mp3")
        tts.save(audio_path)

        pygame.mixer.music.load(audio_path)
        pygame.mixer.music.play()

        # Wait for audio to finish playing
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)

        # Explicitly stop and unload the music
        pygame.mixer.music.stop()
        pygame.mixer.music.unload() # This is crucial for releasing the file handle

        # Give a very tiny moment for the OS to catch up after unload
        time.sleep(0.05)

        # Clean up
        try:
            os.unlink(audio_path)
        except Exception as e:
            st.warning(f"Failed to remove audio file: {e}")

        return True
    except Exception as e:
        st.error(f"Audio playback error: {str(e)}")
        return False

def advanced_speech_recognition():
    r = sr.Recognizer()
    mic = sr.Microphone()
    try:
        with mic as source:
            st.info("🎤 Calibrating microphone...")
            r.adjust_for_ambient_noise(source, duration=2)
            r.energy_threshold = 300
            r.dynamic_energy_threshold = True
            r.pause_threshold = 2.0
        st.success("🎤 Ready to listen! Speak now...")
        with mic as source:
            audio = r.listen(source, timeout=120, phrase_time_limit=120)
        st.info("🔄 Processing your response...")
        try: return r.recognize_google(audio, language='en-US')
        except sr.UnknownValueError:
            try: return r.recognize_google(audio, language='en-IN')
            except: return "UNCLEAR"
    except sr.WaitTimeoutError: return "TIMEOUT"
    except sr.RequestError as e: return f"ERROR: {str(e)}"
    except Exception as e: return f"ERROR: {str(e)}"

def should_continue_interview_naturally(conversation, resume_text, jd):
    prompt = f"""Decide whether to CONTINUE or END interview. CONTINUE if more to explore (skills, projects, gaps, behavioral). END if sufficient info gathered. Respond: CONTINUE:focus_area or END. JD: {jd[:1000]} Resume: {resume_text[:1500]} Conv: {conversation[-3000:]}"""
    return generate_ai_content(prompt).strip()

def generate_next_question_dynamically(conversation, resume_text, jd, focus_area):
    prompt = f"""Generate next conversational interview question. Build on last response. Explore skills, projects, examples. Avoid generic questions. Focus area: {focus_area}. Resume: {resume_text[:1500]} JD: {jd[:1000]} Recent Conv: {conversation[-2000:]}"""
    return generate_ai_content(prompt)

# --- Session State ---
for key in ['conversation', 'current_question', 'question_count', 'interview_active', 'listening_active', 'interview_completed', 'jd', 'resume_text', 'feedback_generated']:
    if key not in st.session_state:
        st.session_state[key] = "" if key not in ['question_count', 'interview_active', 'listening_active', 'interview_completed'] else 0 if key == 'question_count' else False

# --- Navigation ---
option = st.sidebar.selectbox("Choose Function", ["Resume Evaluation", "Phonecall Interview"])

# --- Resume Evaluation ---
if option == "Resume Evaluation":
    st.title("📋 Resume Evaluation")
    col1, col2 = st.columns(2)
    with col1:
        st.header("Job Description")
        method = st.radio("Input Method", ["URL", "Text"])
        jd_input = ""
        if method == "URL":
            jd_url_input = st.text_input("Job Description URL")
            if jd_url_input:
                jd_input = extract_jd_from_url(jd_url_input)
                st.text_area("Extracted Job Description", jd_input, height=150)
            else:
                st.text_area("Extracted Job Description", "Enter a URL above to extract JD", height=150)
        else: # Method is "Text"
            jd_input = st.text_area("Paste Job Description", height=150)
        
        # Ensure jd variable is set for the evaluation logic
        jd = jd_input

    with col2:
        st.header("Upload Resume")
        uploaded_file = st.file_uploader("PDF Resume", type="pdf")

    if uploaded_file and jd and jd.strip():
        if st.button("🔍 Evaluate Resume"):
            resume_text = extract_text_from_pdf(uploaded_file)
            jd_words = set(jd.lower().split())
            resume_words = set(resume_text.lower().split())
            score = round((len(jd_words & resume_words) / len(jd_words)) * 100, 2) if jd_words else 0
            st.metric("Resume Match Score", f"{score}%")
            feedback = generate_ai_content(f"Analyze resume against JD. Provide: Strengths, Missing skills, Suggestions, Summary, Professional feedback. JD: {jd} Resume: {resume_text}")
            st.markdown("### 📝 AI Feedback")
            st.write(feedback)
            pdf_path = save_pdf("Resume Evaluation Report", f"Match: {score}%\n\n{resume_text}", feedback)
            with open(pdf_path, "rb") as f: st.download_button("📄 Download Feedback as PDF", f, file_name="resume_feedback.pdf")
    elif st.button("🔍 Evaluate Resume"): # Button clicked but inputs are missing
        if not uploaded_file:
            st.warning("Please upload a resume to evaluate.")
        if not (jd and jd.strip()):
            st.warning("Please provide a Job Description (URL or text) to evaluate.")

# --- Automated Phone Interview ---
elif option == "Phonecall Interview":
    st.title("📱 Phonecall Interview with AI")

    if not st.session_state.interview_active and not st.session_state.interview_completed:
        st.markdown("""
        ### 🎯 Natural AI Interview Experience:
        * *Adaptive Questioning*: AI asks questions based on your responses and background.
        * *Extended Speaking Time*: Up to 2 minutes per response.
        * *Conversational Flow*: Questions build on what you say.
        """)
        col1, col2 = st.columns(2)
        with col1:
            st.header("Job Description")
            method = st.radio("Input Method", ["URL", "Text"], key="jd_method")
            jd_url_input = ""
            if method == "URL":
                jd_url_input = st.text_input("Job Description URL", key="jd_url")
                if jd_url_input:
                    st.session_state.jd = extract_jd_from_url(jd_url_input)
                    st.text_area("Extracted Job Description", st.session_state.jd, height=150, key="jd_text_display_url")
                else:
                    st.session_state.jd = "" # Ensure JD is empty if URL input is empty
                    st.text_area("Extracted Job Description", "Enter a URL above to extract JD", height=150, key="jd_text_display_url")
            else: # Method is "Text"
                st.session_state.jd = st.text_area("Paste Job Description", height=150, key="jd_text_manual")

        with col2:
            st.header("Upload Resume")
            uploaded_file = st.file_uploader("PDF Resume", type="pdf")

        if uploaded_file and st.session_state.jd and st.session_state.jd.strip() != "":
            st.success("✅ Ready to start adaptive interview!")
            if st.button("📞 Start AI Interview", type="primary"):
                st.session_state.resume_text = extract_text_from_pdf(uploaded_file)
                st.info("🤖 AI is analyzing your background...")
                analysis = analyze_resume_for_context(st.session_state.resume_text, st.session_state.jd)
                question = generate_ai_content(f"You are starting a natural interview. Greet and ask one opening question tailored to candidate's background. Background Analysis: {analysis} JD: {st.session_state.jd} Resume: {st.session_state.resume_text}")
                st.session_state.conversation = f"AI Interviewer: {question}\n\n"
                st.session_state.current_question = question
                st.session_state.question_count = 1
                st.session_state.interview_active = True
                st.session_state.listening_active = True
                st.rerun()
        else:
            if st.button("📞 Start AI Interview", type="primary"): # Button clicked but inputs are missing
                if not uploaded_file:
                    st.warning("Please upload your resume to start the interview.")
                if not st.session_state.jd.strip():
                    st.warning("Please provide a Job Description (URL or text) to start the interview.")


    elif st.session_state.interview_active:
        st.header("📞 AI Interview In Progress")
        st.info(f"💬 Question {st.session_state.question_count} - AI is adapting to your responses")
        st.subheader("Current Question")

        # Webcam Integration HTML component
        components.html("""
        <style>
        #draggable-webcam {
          position: fixed; top: 80px; right: 20px; z-index: 9999; cursor: grab;
          background: #222; border: 3px solid #007bff; border-radius: 12px; padding: 5px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.3); display: flex; flex-direction: column;
          align-items: center; justify-content: center;
        }
        #draggable-webcam video {
          width: 220px; height: 165px; border-radius: 8px; background-color: black;
        }
        #webcam-status { color: #fff; font-size: 0.8em; margin-top: 5px; }
        </style>
        <div id="draggable-webcam">
          <video id="webcam" autoplay playsinline></video>
          <div id="webcam-status">Webcam Loading...</div>
        </div>
        <script>
        let drag = document.getElementById("draggable-webcam");
        let webcamStatus = document.getElementById("webcam-status");
        let offsetX, offsetY, isDown = false;
        drag.addEventListener('mousedown', function(e) {
          isDown = true; offsetX = drag.offsetLeft - e.clientX; offsetY = drag.offsetTop - e.clientY;
          drag.style.cursor = 'grabbing';
        });
        document.addEventListener('mouseup', () => { isDown = false; drag.style.cursor = 'grab'; });
        document.addEventListener('mousemove', function(e) {
          if (!isDown) return; e.preventDefault();
          drag.style.left = (e.clientX + offsetX) + 'px'; drag.style.top = (e.clientY + offsetY) + 'px';
        });
        navigator.mediaDevices.getUserMedia({ video: true, audio: false })
        .then(stream => { document.getElementById('webcam').srcObject = stream; webcamStatus.textContent = 'Webcam Active'; })
        .catch(err => { console.error("Webcam error:", err); webcamStatus.textContent = 'Webcam Blocked/Error'; drag.style.borderColor = '#dc3545'; });
        </script>
        """, height=300)

        if st.session_state.current_question and st.session_state.listening_active:
            st.info("🤖 AI is asking the question...")
            with st.spinner("Playing question..."):
                text_to_speech_and_play(st.session_state.current_question)
            st.success("🎤 Ready to listen! You have up to 2 minutes to respond.")
            user_response = advanced_speech_recognition()
            if user_response == "TIMEOUT":
                st.warning("⏰ No response detected.")
                user_response = "[No response - timeout]"
            elif user_response == "UNCLEAR":
                st.warning("🔇 Could not understand response.")
                user_response = "[Response unclear]"
            elif user_response.startswith("ERROR"):
                st.error(f"❌ {user_response}")
                user_response = "[Audio error]"
            else:
                st.success(f"✅ Recorded your response: {user_response[:50]}...")
            st.session_state.conversation += f"Candidate: {user_response}\n\n"

            decision = should_continue_interview_naturally(st.session_state.conversation, st.session_state.resume_text, st.session_state.jd)
            if decision.startswith("CONTINUE"):
                focus_area = decision.split(":")[1] if ":" in decision else "general"
                with st.spinner("AI is thinking..."):
                    next_question = generate_next_question_dynamically(st.session_state.conversation, st.session_state.resume_text, st.session_state.jd, focus_area)
                st.session_state.conversation += f"AI Interviewer: {next_question}\n\n"
                st.session_state.current_question = next_question
                st.session_state.question_count += 1
                time.sleep(1); st.rerun()
            else:
                closing_message = "Thank you. The interview is now complete."
                st.session_state.conversation += f"AI Interviewer: {closing_message}\n\n"
                text_to_speech_and_play(closing_message)
                st.session_state.interview_active = False
                st.session_state.interview_completed = True
                st.session_state.listening_active = False
                st.success(f"🎉 Interview completed! AI asked {st.session_state.question_count} questions.")
                st.rerun()

        st.sidebar.markdown("### Interview Controls")
        if st.sidebar.button("⏭ Skip Question"): st.session_state.conversation += "Candidate: [Question skipped]\n\n"; st.rerun()
        if st.sidebar.button("🛑 End Interview"): st.session_state.interview_active = False; st.session_state.interview_completed = True; st.rerun()
        with st.expander("📝 Live Interview Transcript"): st.text_area("Conversation", st.session_state.conversation, height=400, key="live_transcript")

    elif st.session_state.interview_completed:
        st.success(f"🎉 Interview Completed!")
        st.markdown(f"### Interview Summary:\n* *Total Questions: {st.session_state.question_count}\n *Interview Type: Conversational\n *Coverage*: Adaptive to your profile")

        if not st.session_state.feedback_generated:
            with st.spinner("🤖 AI is generating comprehensive feedback..."):
                feedback_prompt = f"""Provide COMPREHENSIVE professional interview feedback. Include: Overall Score, Communication, Technical Competency, Behavioral Assessment, Experience Evaluation, Cultural Fit, Response Quality, Key Strengths, Development Areas, Highlights, Hiring Recommendation, Negotiation Position, Next Steps. JD: {st.session_state.jd} Transcript: {st.session_state.conversation}"""
                st.session_state.feedback_generated = generate_ai_content(feedback_prompt)

        st.markdown("### 📋 Professional Interview Assessment")
        st.write(st.session_state.feedback_generated)

        col1, col2, col3 = st.columns(3)
        with col1:
            pdf_path = save_pdf(f"Interview Assessment - {st.session_state.question_count} Qs", st.session_state.conversation, st.session_state.feedback_generated)
            with open(pdf_path, "rb") as f: st.download_button("📄 Download Full Assessment", f, file_name=f"interview_assessment_{uuid.uuid4().hex[:8]}.pdf", type="primary")
        with col2:
            transcript_path = save_pdf("Interview Transcript Only", st.session_state.conversation)
            with open(transcript_path, "rb") as f: st.download_button("📝 Download Transcript Only", f, file_name=f"interview_transcript_{uuid.uuid4().hex[:8]}.pdf")
        with col3:
            if st.button("🔄 Start New Interview"):
                # Reset all session state variables except for the API key
                for key in list(st.session_state.keys()):
                    if key != 'api_key': # Keep the API key set
                        del st.session_state[key]
                # Re-initialize the necessary session state variables
                st.session_state.conversation = ""
                st.session_state.current_question = ""
                st.session_state.question_count = 0
                st.session_state.interview_active = False
                st.session_state.listening_active = False
                st.session_state.interview_completed = False
                st.rerun()

# --- Enhanced Styling ---
st.markdown("""
<style>
.stButton > button { width: 100%; }
.main .block-container { padding-top: 2rem; }
.stAlert > div { padding: 1rem; }
.stProgress > div > div > div { background-color: #1f77b4; }
.stMetric { background-color: #f0f2f6; padding: 1rem; border-radius: 0.5rem; }
</style>
""", unsafe_allow_html=True)