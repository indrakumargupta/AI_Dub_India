import os
import uuid
import subprocess
import tempfile
import time 
from flask import Flask, request, jsonify, send_from_directory
from gtts import gTTS
import google.generativeai as genai

# ================= CONFIGURATION =================
# 🔑 Add your Gemini API key here:
genai.configure(api_key="AIzaSyCVC1B2Ly7fJsFyy5A-ecQ1hQgjHWhNSKw")
genai.configure(api_key="your api key")

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "outputs"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder=OUTPUT_FOLDER)

# ================= HELPER FUNCTIONS =================
def run_ffmpeg(cmd):
"""Run ffmpeg command safely"""
print("FFMPEG:", " ".join(cmd))
proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if proc.returncode != 0:
# Raise the detailed FFmpeg error message
raise RuntimeError(f"FFmpeg failed with error:\n{proc.stderr.decode('utf-8')}")

def extract_audio_to_wav(video_path, out_wav_path):
"""Extract mono 16kHz WAV audio, suitable for Gemini transcription."""
cmd = ["ffmpeg", "-y", "-i", video_path, "-ac", "1", "-ar", "16000", "-vn", out_wav_path]
run_ffmpeg(cmd)

def transcribe_audio_with_gemini(wav_path):
"""
   FIX: Implements real transcription. 
   NOTE: Changed 'file=wav_path' to 'path=wav_path' for broader SDK compatibility 
   based on the 'unexpected keyword argument file' error in the screenshot.
   """
print(f"Uploading and transcribing audio file: {wav_path}")

uploaded_file = None
try:
# 1. Upload the file with retry logic
for attempt in range(3):
try:
# *** CRITICAL FIX HERE: Using path= instead of file= ***
uploaded_file = genai.upload_file(path=wav_path)
print(f"File uploaded successfully: {uploaded_file.name}")
break
except Exception as e:
print(f"Upload attempt {attempt+1} failed: {e}")
time.sleep(2 ** attempt) 

if uploaded_file is None:
raise RuntimeError("Failed to upload audio file to Gemini API after multiple retries.")

# 2. Transcribe the file
prompt = "Transcribe the audio accurately. Only return the transcribed text."
model = genai.GenerativeModel("gemini-2.5-flash")

transcript = ""
for attempt in range(3):
try:
# Pass both the prompt and the uploaded file object
response = model.generate_content([prompt, uploaded_file])
transcript = response.text.strip()
print(f"Transcription received (Attempt {attempt+1}): {transcript[:50]}...")
return transcript
except Exception as e:
print(f"Transcription attempt {attempt+1} failed: {e}")
time.sleep(2 ** attempt) 

# If all attempts fail, raise an error
raise RuntimeError("Failed to get transcription from Gemini API after multiple retries.")

finally:
# 3. Delete the uploaded file from the service (CRUCIAL CLEANUP)
if uploaded_file:
print(f"Deleting uploaded file: {uploaded_file.name}")
for attempt in range(3):
try:
genai.delete_file(name=uploaded_file.name)
break
except Exception as e:
print(f"Deletion attempt {attempt+1} failed: {e}")
time.sleep(2 ** attempt)


def gemini_clean_and_translate(text, target_lang):
"""Use Gemini API to clean and translate"""
prompt = (
f"Translate this text to {target_lang} and make sure the phrasing sounds "
f"natural and fluent for a native speaker:\n\n{text}"
)
model = genai.GenerativeModel("gemini-2.5-flash")
print(f"Calling Gemini for translation to {target_lang}...")

for attempt in range(3):
try:
response = model.generate_content(prompt)
if response.text:
translated_text = response.text
print(f"Translation received (Attempt {attempt+1}): {translated_text[:50]}...")
return translated_text
else:
print(f"Attempt {attempt+1} failed: No text in response.")
except Exception as e:
print(f"Translation attempt {attempt+1} failed with error: {e}")
time.sleep(2 ** attempt) 

return text

def synthesize_with_gtts(text, lang_code="en"): 
"""Generate voice from text using gTTS"""
tmp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
print(f"Synthesizing TTS in language code: {lang_code}")
try:
tts = gTTS(text=text, lang=lang_code, slow=False)
tts.save(tmp_mp3.name)
return tmp_mp3.name
except Exception as e:
print(f"gTTS failed: {e}. Check if language code '{lang_code}' is supported.")
os.unlink(tmp_mp3.name)
raise

def apply_voice_effect(input_audio, voice_style="female"):
"""
   Add pitch/tempo adjustments to simulate male/female voice.
   Tempo is set to 0.7 (30% slower than neutral) for a very slow, deliberate pace.
   """
output_audio = input_audio.replace(".mp3", f"_{voice_style}.mp3")

# *** CRITICAL CHANGE: Set atempo to 0.7 for 30% slower speed ***
SLOW_TEMPO = "0.7"

if voice_style.lower() == "male":
# Lower pitch, very slow tempo (atempo=0.7)
cmd = ["ffmpeg", "-y", "-i", input_audio, "-af", f"asetrate=44100*0.9,atempo={SLOW_TEMPO}", output_audio]
else:
# Higher pitch, very slow tempo (atempo=0.7)
cmd = ["ffmpeg", "-y", "-i", input_audio, "-af", f"asetrate=44100*1.1,atempo={SLOW_TEMPO}", output_audio]

run_ffmpeg(cmd)
return output_audio

def mux_audio_to_video(video_path, audio_path, out_path):
"""Merge dubbed audio back into video"""
cmd = [
"ffmpeg", "-y",
"-i", video_path,
"-i", audio_path,
"-c:v", "copy",
"-map", "0:v:0",
"-map", "1:a:0",
"-shortest",
out_path
]
run_ffmpeg(cmd)

# ================= ROUTES =================
@app.route("/api/dubbing/upload", methods=["POST"])
def upload_and_dub():
# Variables to track file paths for cleanup
video_path = None
wav_path = None
tts_mp3 = None
tts_modified = None

try:
video_file = request.files.get("file")
target_lang = request.form.get("targetLang", "English")
voice_style = request.form.get("voiceStyle", "female")

if not video_file:
return jsonify({"error": "No file uploaded"}), 400

uid = uuid.uuid4().hex[:8]
video_path = os.path.join(UPLOAD_FOLDER, f"{uid}_{video_file.filename}")
video_file.save(video_path)

# Step 1: Extract Audio
wav_path = os.path.join(UPLOAD_FOLDER, f"{uid}.wav")
extract_audio_to_wav(video_path, wav_path)

# Step 2: Transcribe
transcript = transcribe_audio_with_gemini(wav_path)

if not transcript.strip():
# If transcription fails to return text, return a specific error
raise Exception("Gemini returned an empty transcript. The audio may be silent or could not be processed.")

# Step 3: Translate using Gemini
processed_text = gemini_clean_and_translate(transcript, target_lang)

# Step 4: gTTS synthesis
gtts_lang_map = {
"Hindi": "hi", "English": "en", "Tamil": "ta", "Telugu": "te", "Bengali": "bn", 
"Japanese": "ja", "Spanish": "es", "French": "fr", "German": "de", "Korean": "ko"
}
gtts_lang = gtts_lang_map.get(target_lang, "en")
tts_mp3 = synthesize_with_gtts(processed_text, gtts_lang)

# Step 5: Apply voice style (Speed has been adjusted here)
tts_modified = apply_voice_effect(tts_mp3, voice_style)

# Step 6: Merge dubbed audio with video
out_video_path = os.path.join(OUTPUT_FOLDER, f"dubbed_{uid}.mp4")
mux_audio_to_video(video_path, tts_modified, out_video_path)

return jsonify({
"message": "✅ Dubbing complete!",
"transcript": transcript,
"translated_text": processed_text,
"output": f"/{os.path.basename(out_video_path)}"
})

except RuntimeError as e:
print("❌ FFmpeg Operation Error:", str(e))
return jsonify({"error": f"FFmpeg Error: Audio/Video processing failed. Details: {e}"}), 500
except Exception as e:
print("❌ General Error:", str(e))
return jsonify({"error": f"An unexpected error occurred during dubbing: {e}"}), 500
finally:
# Final cleanup of all temporary files
if wav_path and os.path.exists(wav_path):
try: os.unlink(wav_path)
except Exception as e: print(f"Error deleting {wav_path}: {e}")
if tts_mp3 and os.path.exists(tts_mp3):
try: os.unlink(tts_mp3)
except Exception as e: print(f"Error deleting {tts_mp3}: {e}")
if tts_modified and os.path.exists(tts_modified):
try: os.unlink(tts_modified)
except Exception as e: print(f"Error deleting {tts_modified}: {e}")
if video_path and os.path.exists(video_path):
try: os.unlink(video_path)
except Exception as e: print(f"Error deleting {video_path}: {e}")

@app.route("/<path:filename>")
def serve_output(filename):
return send_from_directory(OUTPUT_FOLDER, filename)

if __name__ == "__main__":
print("🎙️ Video Dubbing Backend Running Successfully!")
    app.run(debug=True, port=5000)

    app.run(debug=True, port=5000)