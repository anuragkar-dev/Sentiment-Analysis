from flask import Blueprint, Flask, current_app, render_template, request, jsonify, Response
import cv2
from flask_login import current_user, login_required
import numpy as np
import librosa
from sklearn.linear_model import LogisticRegression
import sounddevice as sd
import threading
import queue
import os
import glob
import joblib
from werkzeug.utils import secure_filename
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
import warnings
from deepface import DeepFace
import tensorflow as tf
import shutil
import mysql.connector
import json
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
import matplotlib.pyplot as plt
import seaborn as sns
from secret import db_host, db_user, db_password, db_database  # Importing MySQL DB credentials from secret.py
warnings.filterwarnings('ignore')

analysis = Blueprint(
    'analysis',  # Blueprint name
    __name__,
    static_folder='static',
    template_folder='templates'
)

# Blueprint-specific configurations
analysis.config = {
    'UPLOAD_FOLDER': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads'),
    'MAX_CONTENT_LENGTH': 16 * 1024 * 1024  # 16MB max file size
}

os.makedirs(analysis.config['UPLOAD_FOLDER'], exist_ok=True)

# Recreate models directory
os.makedirs('models', exist_ok=True)

audio_chunk_size = 22050  # 1 second at 22050 Hz
recording_stream = None
recording_data = []

def audio_callback(indata, frames, time, status):
    """Callback for audio recording"""
    if status:
        print(status)
    recording_data.append(indata.copy())

class EmotionAnalyzer:
    def __init__(self):
        self.sample_rate = 22050
        self.frame_length = 2048
        self.hop_length = 512
        self.dataset_path = "archive"
        
        # Initialize scaler and model
        self.scaler = None
        self.audio_model = None
        
        # Try to load existing model and scaler
        try:
            self.audio_model = joblib.load('models/audio_emotion_model.joblib')
            self.scaler = joblib.load('models/audio_scaler.joblib')
            print("✅ Loaded existing audio model and scaler")
        except:
            print("⚠️ No existing model found, will need to train new model")
            # Train new model if needed
            self.train_audio_model()
        
        # Initialize DeepFace model
        try:
            tf.get_logger().setLevel('ERROR')
            _ = DeepFace.analyze(np.zeros((48, 48, 3), dtype=np.uint8), 
                               actions=['emotion'],
                               enforce_detection=False,
                               detector_backend='retinaface')
            print("✅ Loaded emotion detection model")
        except Exception as e:
            print(f"⚠️ Warning: {e}")

    def extract_audio_features(self, audio_data):
        """Simplified and robust audio feature extraction - exactly 30 features"""
        try:
            if len(audio_data.shape) > 1:
                audio_data = audio_data.mean(axis=1)
            
            audio_data = librosa.util.normalize(audio_data)
            features = {}
            
            # Basic MFCC features (13 coefficients)
            mfccs = librosa.feature.mfcc(y=audio_data, sr=self.sample_rate, n_mfcc=13)
            features['mfcc_mean'] = np.mean(mfccs, axis=1)  # 13 features
            features['mfcc_std'] = np.std(mfccs, axis=1)    # 13 features
            
            # Basic spectral features (4 features)
            features['spectral_centroid'] = np.mean(librosa.feature.spectral_centroid(y=audio_data, sr=self.sample_rate))
            features['spectral_bandwidth'] = np.mean(librosa.feature.spectral_bandwidth(y=audio_data, sr=self.sample_rate))
            features['spectral_rolloff'] = np.mean(librosa.feature.spectral_rolloff(y=audio_data, sr=self.sample_rate))
            features['zero_crossing_rate'] = np.mean(librosa.feature.zero_crossing_rate(audio_data))
            
            return features
            
        except Exception as e:
            print(f"Error extracting audio features: {e}")
            return None

    def train_audio_model(self):
        """Train the audio emotion model with current feature set"""
        try:
            print("Training new audio emotion model...")
            
            # Emotion mapping for RAVDESS dataset
            emotion_map = {
                "01": "neutral",
                "02": "calm",
                "03": "happy",
                "04": "sad",
                "05": "angry",
                "06": "fearful",
                "07": "disgust",
                "08": "surprised"
            }
            
            # Initialize lists to store features and labels
            all_features = []
            all_labels = []
            
            # Process each audio file in the dataset
            for emotion_folder in os.listdir(self.dataset_path):
                emotion_path = os.path.join(self.dataset_path, emotion_folder)
                if not os.path.isdir(emotion_path):
                    continue
                    
                print(f"Processing {emotion_folder} files...")
                for audio_file in glob.glob(os.path.join(emotion_path, "*.wav")):
                    try:
                        # Extract emotion from filename (third position in RAVDESS format)
                        filename = os.path.basename(audio_file)
                        emotion_code = filename.split("-")[2]
                        emotion = emotion_map.get(emotion_code, "unknown")
                        
                        if emotion == "unknown":
                            continue
                        
                        # Load and process audio file
                        audio_data, _ = librosa.load(audio_file, sr=self.sample_rate)
                        features = self.extract_audio_features(audio_data)
                        
                        if features:
                            # Convert features to vector
                            feature_vector = []
                            feature_vector.extend(features['mfcc_mean'])
                            feature_vector.extend(features['mfcc_std'])
                            feature_vector.append(features['spectral_centroid'])
                            feature_vector.append(features['spectral_bandwidth'])
                            feature_vector.append(features['spectral_rolloff'])
                            feature_vector.append(features['zero_crossing_rate'])
                            
                            all_features.append(feature_vector)
                            all_labels.append(emotion)
                            
                    except Exception as e:
                        print(f"Error processing file {audio_file}: {e}")
                        continue
            
            if not all_features:
                raise Exception("No features extracted from dataset")
                
            # Convert to numpy arrays
            X = np.array(all_features)
            y = np.array(all_labels)
            
            # Split the data
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42
            )
            
            # Initialize and fit the scaler
            self.scaler = StandardScaler()
            X_train_scaled = self.scaler.fit_transform(X_train)
            X_test_scaled = self.scaler.transform(X_test)
            
            # Train the model
            self.audio_model = RandomForestClassifier(
                n_estimators=100,
                max_depth=None,
                n_jobs=-1,
                random_state=42
            )
            self.audio_model.fit(X_train_scaled, y_train)
            
            # Evaluate the model
            y_pred = self.audio_model.predict(X_test_scaled)
            print("\nModel Performance:")
            print(classification_report(y_test, y_pred))
            
            # Save the model and scaler
            os.makedirs('models', exist_ok=True)
            joblib.dump(self.audio_model, 'models/audio_emotion_model.joblib')
            joblib.dump(self.scaler, 'models/audio_scaler.joblib')
            print("✅ Model and scaler saved successfully")
            
        except Exception as e:
            print(f"❌ Error training model: {e}")
            self.audio_model = None
            self.scaler = None

    def store_emotion_data(self, user_id, emotion_data, file, file_type):
        """Store the emotion data along with the file in the database."""
        try:
            # Establish DB connection
            conn = mysql.connector.connect(
                host=db_host,
                user=db_user,
                password=db_password,
                database=db_database
            )
            cursor = conn.cursor()
            
            # Convert emotion_data to JSON format
            emotion_data_json = json.dumps(emotion_data)
            # Check the type of 'file' to ensure it's a byte object

            # Prepare the insert statement
            query = """
                INSERT INTO user_emotions (user_id, emotion_data, file, file_type)
                VALUES (%s, %s, %s, %s)
            """
            
            # Execute the query
            cursor.execute(query, (user_id, emotion_data_json, file, file_type))
            conn.commit()
            
            # Close the connection
            cursor.close()
            conn.close()
        except mysql.connector.Error as e:
            print(f"Error storing emotion data: {str(e)}")
            
    def analyze_image(self, image):
        """Image analysis with reliable face detection"""
        try:
            # Ensure consistent image format
            if len(image.shape) == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            
            # Basic preprocessing - avoid excessive processing that might affect detection
            if image.shape[1] > 1000:  # Only resize if image is too large
                scale = 1000 / image.shape[1]
                image = cv2.resize(image, (0, 0), fx=scale, fy=scale)
            
            # Use DeepFace with reliable settings
            results = DeepFace.analyze(
                image,
                actions=['emotion'],
                enforce_detection=False,
                detector_backend='opencv'  # Revert to opencv for reliability
            )
            
            # Handle single result
            if isinstance(results, dict):
                results = [results]
            
            # Format results
            formatted_results = []
            for face_result in results:
                emotions = face_result['emotion']
                # Sort emotions by confidence
                sorted_emotions = sorted(emotions.items(), key=lambda x: x[1], reverse=True)
                
                formatted_results.append({
                    'dominant_emotion': {
                        'name': sorted_emotions[0][0].capitalize(),
                        'confidence': round(sorted_emotions[0][1], 1)
                    },
                    'other_emotions': [
                        {
                            'name': emotion[0].capitalize(),
                            'confidence': round(emotion[1], 1)
                        }
                        for emotion in sorted_emotions[1:]
                        if emotion[1] > 5  # Only show emotions with >5% confidence
                    ]
                })
            # Store the image (as a LONGBLOB) and emotion data in the database
            # Convert image to bytes
            _, img_bytes = cv2.imencode('.jpg', image)
            img_blob = img_bytes.tobytes()
            
            # Store emotion data and file in the database
            self.store_emotion_data(current_user.id, formatted_results, img_blob, 'image')
            return formatted_results
        
        except Exception as e:
            print(f"Error in face analysis: {str(e)}")
            return []
        
    def analyze_frames(self, image):
        """Image analysis with reliable face detection"""
        try:
            # Ensure consistent image format
            if len(image.shape) == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            
            # Basic preprocessing - avoid excessive processing that might affect detection
            if image.shape[1] > 1000:  # Only resize if image is too large
                scale = 1000 / image.shape[1]
                image = cv2.resize(image, (0, 0), fx=scale, fy=scale)
            
            # Use DeepFace with reliable settings
            results = DeepFace.analyze(
                image,
                actions=['emotion'],
                enforce_detection=False,
                detector_backend='opencv'  # Revert to opencv for reliability
            )
            
            # Handle single result
            if isinstance(results, dict):
                results = [results]
            
            # Format results
            formatted_results = []
            for face_result in results:
                emotions = face_result['emotion']
                # Sort emotions by confidence
                sorted_emotions = sorted(emotions.items(), key=lambda x: x[1], reverse=True)
                
                formatted_results.append({
                    'dominant_emotion': {
                        'name': sorted_emotions[0][0].capitalize(),
                        'confidence': round(sorted_emotions[0][1], 1)
                    },
                    'other_emotions': [
                        {
                            'name': emotion[0].capitalize(),
                            'confidence': round(emotion[1], 1)
                        }
                        for emotion in sorted_emotions[1:]
                        if emotion[1] > 5  # Only show emotions with >5% confidence
                    ]
                })
            # Store the image (as a LONGBLOB) and emotion data in the database
            # Convert image to bytes
            _, img_bytes = cv2.imencode('.jpg', image)
            img_blob = img_bytes.tobytes()
            
            # Store emotion data and file in the database
            return formatted_results
        
        except Exception as e:
            print(f"Error in face analysis: {str(e)}")
            return []
        
    def analyze_audio(self, audio_path):
        """Analyze emotions in an audio file"""
        try:
            if self.audio_model is None or self.scaler is None:
                return {"error": "Model not initialized. Please train the model first."}
            
            # Load audio file
            audio_data, _ = librosa.load(audio_path, sr=self.sample_rate)
            
            # Extract features
            features = self.extract_audio_features(audio_data)
            if not features:
                return {"error": "Could not extract features from audio"}
            
            # Prepare feature vector - exactly 30 features
            feature_vector = []
            # MFCCs (26 features)
            feature_vector.extend(features['mfcc_mean'])  # 13 features
            feature_vector.extend(features['mfcc_std'])   # 13 features
            # Spectral features (4 features)
            feature_vector.append(features['spectral_centroid'])
            feature_vector.append(features['spectral_bandwidth'])
            feature_vector.append(features['spectral_rolloff'])
            feature_vector.append(features['zero_crossing_rate'])
            
            # Verify feature count
            if len(feature_vector) != 30:
                print(f"Feature vector length mismatch: got {len(feature_vector)}, expected 30")
                return {"error": "Feature extraction error"}
            
            # Scale features
            try:
                scaled_features = self.scaler.transform([feature_vector])
            except Exception as e:
                print(f"Error scaling features: {e}")
                return {"error": "Error scaling features"}
            
            # Get prediction probabilities
            proba = self.audio_model.predict_proba(scaled_features)[0]
            emotions = self.audio_model.classes_
            
            # Create results dictionary
            results = {emotion: float(prob) for emotion, prob in zip(emotions, proba)}
            
            # Convert audio file to binary blob
            with open(audio_path, 'rb') as audio_file:
                audio_blob = audio_file.read()
            
            formatted_results = [{
                'dominant_emotion': {
                    'name': max(results, key=results.get),
                    'confidence': max(results.values())
                },
                'other_emotions': [
                    {'name': emotion, 'confidence': float(prob)} 
                    for emotion, prob in results.items() 
                    if prob < max(results.values())
                ]
            }]
            
            # Store emotion data in the database
            self.store_emotion_data(current_user.id, formatted_results, audio_blob, "audio")            
            
            return results
            
        except Exception as e:
            print(f"Error in audio analysis: {e}")
            return {"error": str(e)}

    def analyze_realtime_audio(self, audio_data):
        """Analyze emotions in real-time audio chunk"""
        try:
            # Extract features
            features = self.extract_audio_features(audio_data)
            if not features:
                return {"neutral": 1.0}
            
            # Prepare feature vector
            feature_vector = []
            for feature_group in features.values():
                if isinstance(feature_group, np.ndarray):
                    feature_vector.extend(feature_group.flatten())
                else:
                    feature_vector.append(feature_group)
            
            # Scale features
            scaled_features = self.scaler.transform([feature_vector])
            
            # Get prediction probabilities
            proba = self.audio_model.predict_proba(scaled_features)[0]
            emotions = self.audio_model.classes_
            
            # Create results dictionary
            results = {emotion: float(prob) for emotion, prob in zip(emotions, proba)}
            
            # Convert real-time audio data to binary blob
            audio_blob = audio_data.tobytes()
            formatted_results = [{
                'dominant_emotion': {
                    'name': max(results, key=results.get),
                    'confidence': max(results.values())
                },
                'other_emotions': [
                    {'name': emotion, 'confidence': float(prob)} 
                    for emotion, prob in results.items() 
                    if prob < max(results.values())
                ]
            }]
            # Store emotion data in the database
            self.store_emotion_data(current_user.id, formatted_results, audio_blob, "audio")            
            
            return results
            
        except Exception as e:
            print(f"Error in real-time audio analysis: {e}")
            return {"neutral": 1.0}
            
    def analyze_combined_frame(self, frame, audio_data=None):
        """Analyze both video frame and audio data and store the results."""
        try:
            # Analyze video and audio
            video_results = self.analyze_frames(frame)
            audio_results = self.analyze_realtime_audio(audio_data) if audio_data is not None else None
            
            # Format results for DB storage
            if audio_results is not None:
                audio_blob = audio_data.tobytes()  # Convert audio data to binary blob
                audio_results = [{
                    'dominant_emotion': {
                        'name': max(audio_results, key=audio_results.get),
                        'confidence': max(audio_results.values())
                    },
                    'other_emotions': [
                        {'name': emotion, 'confidence': float(prob)} 
                        for emotion, prob in audio_results.items() 
                        if prob < max(audio_results.values())
                    ]
                }]
                
                # Store audio result in DB
                self.store_emotion_data(current_user.id, audio_results, audio_blob, "audio")
            
            results = {
                'video': video_results,
                'audio': audio_results
            }
            
            return results
        except Exception as e:
            print(f"Error in combined analysis: {e}")
            return {'video': [], 'audio': None}


    def predict_audio_emotion(self, feature_vector, audio_data=None):
        """Predict emotion from audio features"""
        try:
            # Load the model if not already loaded
            model_path = os.path.join('models', 'audio_emotion_model.joblib')
            if not hasattr(self, 'audio_model'):
                if os.path.exists(model_path):
                    self.audio_model = joblib.load(model_path)
                else:
                    return {"emotion": "unknown", "confidence": 0}
            
            # Make prediction
            prediction = self.audio_model.predict_proba([feature_vector])[0]
            emotion_idx = np.argmax(prediction)
            confidence = prediction[emotion_idx]
            
            # Map index to emotion label
            emotion_labels = ['angry', 'happy', 'sad', 'neutral']  # Adjust based on your model
            emotion = emotion_labels[emotion_idx]
            
            # Format results for DB storage
            formatted_results = [{
                'dominant_emotion': {
                    'name': emotion,
                    'confidence': round(confidence, 1)
                },
                'other_emotions': []  # No other emotions in this case
            }]
            
            # Store emotion data in the database (if audio_data is provided)
            if audio_data is not None:
                audio_blob = audio_data.tobytes()  # Convert audio data to binary blob
                self.store_emotion_data(current_user.id, formatted_results, audio_blob, "audio")
                
            return {
                "emotion": emotion,
                "confidence": float(confidence)
            }
        except Exception as e:
            print(f"Error predicting audio emotion: {str(e)}")
            return {"emotion": "unknown", "confidence": 0}

# Global analyzer instance
analyzer = EmotionAnalyzer()

# Add these global variables at the top of the file
combined_audio_data = []
combined_stream = None
tfidf_vectorizer = None
classifier_review = None

@analysis.route('/')
@login_required
def index():
    return render_template('index.html', name=current_user.full_name)

# Function to load and prepare the model (called once when the app starts)
@analysis.before_app_request
def load_and_prepare_model():
    global tfidf_vectorizer, classifier_review

    # Assuming the model preparation is the same as before
    df = pd.read_csv('data/Database Data - Amazon Reviews.csv')
    df['review_text'].fillna('', inplace=True)

    tfidf_vectorizer = TfidfVectorizer(max_features=1000)
    X_text = tfidf_vectorizer.fit_transform(df['review_text'])
    X_rating = df['rating'].values.reshape(-1, 1)
    X = pd.concat([pd.DataFrame(X_text.toarray()), pd.DataFrame(X_rating)], axis=1)

    positive_threshold = 4
    negative_threshold = 2

    df['sentiment_id'] = pd.cut(df['rating'], bins=[-float('inf'), negative_threshold, positive_threshold, float('inf')],
                                 labels=['Negative', 'Neutral', 'Positive'])

    y_rating = df['sentiment_id']
    X_train, X_test, y_train, y_test = train_test_split(X, y_rating, test_size=0.2, random_state=42)

    classifier_review = LogisticRegression(max_iter=1000, class_weight='balanced')
    classifier_review.fit(X_train.iloc[:, :-1], y_train)

# Predict sentiment for the provided text input
def predict_sentiment(user_input):
    global tfidf_vectorizer, classifier_review
    X_user_input = tfidf_vectorizer.transform([user_input])  # Transform the input text
    prediction = classifier_review.predict(X_user_input)  # Get sentiment prediction
    return prediction[0]

# Text analysis route
@analysis.route('/analyze_text', methods=['POST'])
def analyze_text():
    if 'textInput' not in request.form:
        return jsonify({"error": "No text input provided"}), 400

    user_input = request.form['textInput'].strip()
    
    if not user_input:
        return jsonify({"error": "Empty text input"}), 400

    try:
        # Predict sentiment
        sentiment = predict_sentiment(user_input)
        
        emotion_data = {"sentiment": sentiment}
        user_id = current_user.id 
        # Convert the user_input string to bytes (to store as a blob)
        # user_input_bytes = user_input.encode('utf-8')
        # print(f'{user_input = }')
        # print(f'{user_input_bytes = }')
        file_type = "text"
        analyzer.store_emotion_data(user_id, emotion_data, user_input, file_type)
        
        return jsonify({
            "status": "success",
            "sentiment": sentiment
        })

    except Exception as e:
        print(f"Error in text analysis route: {str(e)}")
        return jsonify({
            "error": "Failed to analyze text",
            "details": str(e),
            "suggestions": [
                "Make sure the text input is valid",
                "Try a different text"
            ]
        }), 500
        
@analysis.route('/get_devices')
def get_devices():
    # Get available cameras
    camera_devices = []
    for i in range(5):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                camera_devices.append({"id": i, "name": f"Camera {i}"})
            cap.release()
    
    # Get available microphones
    audio_devices = []
    try:
        devices = sd.query_devices()
        for i, device in enumerate(devices):
            if device['max_input_channels'] > 0:
                audio_devices.append({
                    "id": i,
                    "name": device['name']
                })
    except:
        pass
        
    return jsonify({
        "cameras": camera_devices,
        "microphones": audio_devices
    })

@analysis.route('/analyze_image', methods=['POST'])
def analyze_image():
    if 'image' not in request.files:
        return jsonify({"error": "No image provided"}), 400
        
    file = request.files['image']
    if file.filename == '':
        return jsonify({"error": "No image selected"}), 400
        
    try:
        # Read image file
        filestr = file.read()
        npimg = np.frombuffer(filestr, np.uint8)
        image = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
        
        if image is None:
            return jsonify({"error": "Failed to decode image"}), 400
            
        print(f"Successfully loaded image of shape: {image.shape}")
        
        # Analyze image
        results = analyzer.analyze_image(image)
        
        if not results:
            return jsonify({
                "status": "no_face_detected",
                "message": "No faces detected in the image",
                "debug_info": {
                    "image_shape": image.shape if image is not None else None,
                    "image_type": str(image.dtype) if image is not None else None
                },
                "suggestions": [
                    "Make sure the face is clearly visible",
                    "Ensure good lighting",
                    "Try a different angle",
                    "Make sure the image is not too dark or blurry"
                ]
            }), 200
            
        return jsonify({
            "status": "success",
            "number_of_faces": len(results),
            "results": results
        })
        
    except Exception as e:
        print(f"Detailed error in image analysis route: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Failed to analyze image",
            "details": str(e),
            "suggestions": [
                "Try uploading a different image",
                "Make sure the image format is supported (JPG, PNG)",
                "Ensure the image is not corrupted"
            ]
        }), 500
        

@analysis.route('/analyze_frames', methods=['POST'])
def analyze_frames():
    if 'image' not in request.files:
        return jsonify({"error": "No image provided"}), 400
        
    file = request.files['image']
    if file.filename == '':
        return jsonify({"error": "No image selected"}), 400
        
    try:
        # Read image file
        filestr = file.read()
        npimg = np.frombuffer(filestr, np.uint8)
        image = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
        
        if image is None:
            return jsonify({"error": "Failed to decode image"}), 400
            
        print(f"Successfully loaded image of shape: {image.shape}")
        
        # Analyze image
        results = analyzer.analyze_frames(image)
        
        if not results:
            return jsonify({
                "status": "no_face_detected",
                "message": "No faces detected in the image",
                "debug_info": {
                    "image_shape": image.shape if image is not None else None,
                    "image_type": str(image.dtype) if image is not None else None
                },
                "suggestions": [
                    "Make sure the face is clearly visible",
                    "Ensure good lighting",
                    "Try a different angle",
                    "Make sure the image is not too dark or blurry"
                ]
            }), 200
            
        return jsonify({
            "status": "success",
            "number_of_faces": len(results),
            "results": results
        })
        
    except Exception as e:
        print(f"Detailed error in image analysis route: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Failed to analyze image",
            "details": str(e),
            "suggestions": [
                "Try uploading a different image",
                "Make sure the image format is supported (JPG, PNG)",
                "Ensure the image is not corrupted"
            ]
        }), 500

def gen_frames():
    camera = cv2.VideoCapture(0)
    while True:
        success, frame = camera.read()
        if not success:
            break
        else:
            # Analyze frame
            try:
                # Use DeepFace to detect faces and emotions
                results = DeepFace.analyze(
                    frame,
                    actions=['emotion'],
                    enforce_detection=False,
                    detector_backend='opencv'
                )
                
                # Convert single result to list if necessary
                if isinstance(results, dict):
                    results = [results]
                
                # Draw results on frame
                for result in results:
                    # Get face region
                    face_region = result.get('region', {})
                    x = face_region.get('x', 0)
                    y = face_region.get('y', 0)
                    w = face_region.get('w', 0)
                    h = face_region.get('h', 0)
                    
                    # Draw rectangle around face
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                    
                    # Get dominant emotion
                    emotions = result.get('emotion', {})
                    dominant_emotion = max(emotions.items(), key=lambda x: x[1])
                    
                    # Draw emotion label above rectangle
                    label = f"{dominant_emotion[0]}: {dominant_emotion[1]:.1f}%"
                    cv2.putText(frame, label, (x, y-10), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.9, 
                              (0, 255, 0), 2)
                
            except Exception as e:
                print(f"Error in frame analysis: {e}")
                
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@analysis.route('/video_feed/<int:camera_id>')
def video_feed(camera_id):
    def generate(camera_id):
        camera = cv2.VideoCapture(camera_id)
        if not camera.isOpened():
            print("Error: Could not open camera")
            return
        
        # Set camera properties for better quality
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        camera.set(cv2.CAP_PROP_FPS, 30)
        
        frame_count = 0
        # Store the last valid results
        last_results = None
        
        while True:
            success, frame = camera.read()
            if not success:
                break
            
            frame_count += 1
            # Analyze every 3rd frame for better performance
            if frame_count % 3 == 0:
                try:
                    # Use DeepFace to detect faces and emotions
                    results = DeepFace.analyze(
                        frame,
                        actions=['emotion'],
                        enforce_detection=False,
                        detector_backend='opencv'
                    )
                    
                    # Convert single result to list if necessary
                    if isinstance(results, dict):
                        results = [results]
                    
                    # Update last_results only if face detection was successful
                    last_results = results
                    
                except Exception as e:
                    print(f"Error in frame analysis: {e}")
            
            # Draw results on every frame using the last valid results
            if last_results:
                for result in last_results:
                    # Get face region
                    face_region = result.get('region', {})
                    x = face_region.get('x', 0)
                    y = face_region.get('y', 0)
                    w = face_region.get('w', 0)
                    h = face_region.get('h', 0)
                    
                    # Draw rectangle around face
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                    
                    # Get dominant emotion
                    emotions = result.get('emotion', {})
                    dominant_emotion = max(emotions.items(), key=lambda x: x[1])
                    
                    # Draw emotion label with background for better visibility
                    label = f"{dominant_emotion[0]}: {dominant_emotion[1]:.1f}%"
                    label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)[0]
                    
                    # Draw background rectangle for text
                    cv2.rectangle(frame, 
                                (x, y - label_size[1] - 10), 
                                (x + label_size[0], y), 
                                (0, 255, 0), 
                                cv2.FILLED)
                    
                    # Draw text
                    cv2.putText(frame, label, 
                              (x, y-10), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.9, 
                              (0, 0, 0), 2)  # Black text for better contrast
            
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        
        camera.release()
                   
    return Response(generate(camera_id),
                   mimetype='multipart/x-mixed-replace; boundary=frame')

@analysis.route('/start_video', methods=['POST'])
def start_video():
    try:
        # Test camera access
        cap = cv2.VideoCapture(0)  # Always use default camera
        if not cap.isOpened():
            return jsonify({"error": "Could not access camera"}), 400
        cap.release()
        
        return jsonify({
            "status": "success", 
            "stream_url": "/video_feed/0"  # Always use camera 0
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@analysis.route('/start_audio_recording', methods=['POST'])
def start_audio_recording():
    global recording_stream, recording_data
    try:
        # Clear previous recording data
        recording_data = []
        
        # Get microphone ID from request
        mic_id = request.json.get('microphone_id', 0)
        
        # Initialize the audio stream
        recording_stream = sd.InputStream(
            device=int(mic_id),
            channels=1,
            samplerate=22050,
            blocksize=audio_chunk_size,
            callback=audio_callback
        )
        recording_stream.start()
        
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error starting audio recording: {str(e)}")
        return jsonify({"error": str(e)}), 500

@analysis.route('/stop_audio_recording', methods=['POST'])
def stop_audio_recording():
    global recording_stream, recording_data
    try:
        if recording_stream:
            recording_stream.stop()
            recording_stream.close()
            recording_stream = None
            
            if recording_data:
                # Combine all recorded chunks
                audio_data = np.concatenate(recording_data)
                
                # Extract features
                features = analyzer.extract_audio_features(audio_data)
                
                if features:
                    # Prepare feature vector
                    feature_vector = []
                    for feature_name, feature_value in features.items():
                        if isinstance(feature_value, np.ndarray):
                            feature_vector.extend(feature_value.flatten())
                        else:
                            feature_vector.append(feature_value)
                    
                    # Make prediction
                    emotions = {
                        'angry': 0.0, 'happy': 0.0, 'sad': 0.0, 
                        'neutral': 0.0, 'fearful': 0.0
                    }
                    
                    # Use your trained model here
                    try:
                        model_path = os.path.join('models', 'audio_emotion_model.joblib')
                        if os.path.exists(model_path):
                            model = joblib.load(model_path)
                            prediction = model.predict_proba([feature_vector])[0]
                            emotions_list = ['angry', 'happy', 'sad', 'neutral', 'fearful']
                            for i, prob in enumerate(prediction):
                                emotions[emotions_list[i]] = float(prob)
                        else:
                            # Fallback: simple analysis based on audio features
                            # This is a simplified example - you should replace with your actual logic
                            rms_mean = features['rms_mean']
                            zcr_mean = features['zcr_mean']
                            
                            if rms_mean > 0.1:  # High energy
                                if zcr_mean > 0.1:  # High frequency
                                    emotions['happy'] = 0.7
                                else:
                                    emotions['angry'] = 0.6
                            else:  # Low energy
                                if zcr_mean > 0.1:  # High frequency
                                    emotions['fearful'] = 0.5
                                else:
                                    emotions['sad'] = 0.6
                    
                    except Exception as e:
                        print(f"Error in emotion prediction: {str(e)}")
                        emotions['neutral'] = 1.0
                    
                    # Get dominant emotion
                    dominant_emotion = max(emotions.items(), key=lambda x: x[1])
                    
                    return jsonify({
                        "dominant_emotion": {
                            "emotion": dominant_emotion[0],
                            "confidence": round(dominant_emotion[1] * 100, 1)
                        },
                        "all_emotions": emotions
                    })
            
            # Clear recording data
            recording_data = []
            
        return jsonify({
            "dominant_emotion": {
                "emotion": "neutral",
                "confidence": 100.0
            }
        })
        
    except Exception as e:
        print(f"Error stopping audio recording: {str(e)}")
        return jsonify({"error": str(e)}), 500

@analysis.route('/analyze_audio', methods=['POST'])
def analyze_audio():
    if 'audio' not in request.files:
        return jsonify({"error": "No audio file provided"}), 400
    
    file = request.files['audio']
    if file.filename == '':
        return jsonify({"error": "No audio file selected"}), 400
    
    try:
        # Save and process audio
        filename = secure_filename(file.filename)
        filepath = os.path.join(analysis.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Analyze audio
        results = analyzer.analyze_audio(filepath)
        
        # Get dominant emotion
        dominant_emotion = max(results.items(), key=lambda x: x[1])
        formatted_result = {
            "dominant_emotion": {
                "emotion": dominant_emotion[0],
                "confidence": round(dominant_emotion[1] * 100, 1)
            }
        }
        
        # Clean up
        os.remove(filepath)
        
        return jsonify(formatted_result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@analysis.route('/start_combined', methods=['POST'])
def start_combined():
    global combined_stream, combined_audio_data
    
    try:
        camera_id = request.json.get('camera_id', 0)
        mic_id = request.json.get('microphone_id', 0)
        
        # Reset audio data
        combined_audio_data = []
        
        # Setup audio stream
        combined_stream = sd.InputStream(
            device=mic_id,
            channels=1,
            samplerate=22050,
            blocksize=22050,  # 1 second chunks
            callback=lambda indata, frames, time, status: combined_audio_data.append(indata.copy())
        )
        combined_stream.start()
        
        return jsonify({
            "status": "success",
            "stream_url": f"/combined_feed/{camera_id}"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@analysis.route('/stop_combined', methods=['POST'])
def stop_combined():
    global combined_stream
    
    try:
        if combined_stream:
            combined_stream.stop()
            combined_stream.close()
            combined_stream = None
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@analysis.route('/combined_feed/<int:camera_id>')
def combined_feed(camera_id):
    print('running combined')
    def generate(camera_id):
        camera = cv2.VideoCapture(camera_id)
        while True:
            success, frame = camera.read()
            if not success:
                break
            
            # Get latest audio data
            global combined_audio_data
            audio_chunk = np.concatenate(combined_audio_data) if combined_audio_data else None
            combined_audio_data = []  # Reset for next chunk
            
            # Analyze frame and audio
            results = analyzer.analyze_combined_frame(frame, audio_chunk)
            
            # Draw results on frame
            if results['video']:
                for face in results['video']:
                    box = face['box']
                    emotions = face['emotions']
                    
                    # Draw rectangle around face
                    cv2.rectangle(frame, 
                                (box[0], box[1]), 
                                (box[0] + box[2], box[1] + box[3]), 
                                (0, 255, 0), 2)
                    
                    # Display video emotions
                    y_pos = box[1] - 10
                    for emotion, score in emotions.items():
                        text = f"Face: {emotion}: {score:.2f}"
                        cv2.putText(frame, text, (box[0], y_pos), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, 
                                  (0, 255, 0), 1)
                        y_pos -= 20
            
            # Display audio emotions
            if results['audio']:
                y_pos = 30
                for emotion, score in results['audio'].items():
                    text = f"Audio: {emotion}: {score:.2f}"
                    cv2.putText(frame, text, (10, y_pos), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.5, 
                              (255, 0, 0), 1)
                    y_pos += 20
            
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        
        camera.release()
        print(results)
    return Response(generate(camera_id),
                   mimetype='multipart/x-mixed-replace; boundary=frame')

@analysis.route('/upload_video', methods=['POST'])
def upload_video():
    video_file = request.files['video']
    if video_file:
        # Save the video temporarily to process it
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], video_file.filename)
        video_file.save(upload_path)

        cap = cv2.VideoCapture(upload_path)
        overall_emotions = {}
        frame_skip = 10 # Process every 5th frame (adjust as needed)
        frame_count = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            if frame_count % frame_skip == 0:
                # Analyze the current frame
                result = analyzer.analyze_combined_frame(frame)

                # Aggregate emotions for the video
                if result['video']:
                    for video_result in result['video']:
                        if 'dominant_emotion' in video_result:
                            dominant = video_result['dominant_emotion']['name']
                            confidence = video_result['dominant_emotion']['confidence']
                            if dominant in overall_emotions:
                                overall_emotions[dominant]['confidence_sum'] += confidence
                                overall_emotions[dominant]['count'] += 1
                            else:
                                overall_emotions[dominant] = {'confidence_sum': confidence, 'count': 1}

            frame_count += 1
        
        cap.release()

        # Compute the overall emotions
        if overall_emotions:
            overall_dominant = max(
                overall_emotions.items(), 
                key=lambda item: item[1]['confidence_sum'] / item[1]['count']
            )
            dominant_emotion = {
                'name': overall_dominant[0],
                'confidence': overall_dominant[1]['confidence_sum'] / overall_dominant[1]['count']
            }
            other_emotions = [
                {
                    'name': emotion,
                    'confidence': data['confidence_sum'] / data['count']
                }
                for emotion, data in overall_emotions.items()
                if emotion != dominant_emotion['name']
            ]
        else:
            dominant_emotion = None
            other_emotions = []

        # Store the video binary data along with emotion data
        emotion_data = {
            "dominant_emotion": dominant_emotion,
            "other_emotions": other_emotions
        }
        analyzer.store_emotion_data(
            user_id=current_user.id,
            emotion_data=emotion_data,
            file=open(upload_path, 'rb').read(),  # Store the video as binary
            file_type="video"
        )

        return jsonify({
            "status": "success",
            "message": "Video processed and analyzed!",
            "dominant_emotion": dominant_emotion,
            "other_emotions": other_emotions
        }), 200
    else:
        return jsonify({"error": "No video file provided"}), 400



@analysis.route('/analyze_audio_file', methods=['POST'])
def analyze_audio_file():
    try:
        if 'audio' not in request.files:
            return jsonify({"error": "No audio file provided"}), 400
        
        file = request.files['audio']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400
        
        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(analysis.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            try:
                # Use the analyzer's analyze_audio method directly
                results = analyzer.analyze_audio(filepath)
                
                if "error" in results:
                    return jsonify({"error": results["error"]}), 500
                
                # Get dominant emotion
                dominant_emotion = max(results.items(), key=lambda x: x[1])
                
                # Clean up
                if os.path.exists(filepath):
                    os.remove(filepath)
                
                return jsonify({
                    "dominant_emotion": {
                        "emotion": dominant_emotion[0],
                        "confidence": round(dominant_emotion[1] * 100, 1)
                    },
                    "all_emotions": {
                        k: round(v * 100, 1) for k, v in results.items()
                        if v > 0.1  # Only include emotions with >10% confidence
                    }
                })
                
            except Exception as e:
                print(f"Error processing audio file: {str(e)}")
                if os.path.exists(filepath):
                    os.remove(filepath)
                return jsonify({"error": "Error processing audio file"}), 500
            
    except Exception as e:
        print(f"Error handling audio file upload: {str(e)}")
        return jsonify({"error": "Error handling audio file upload"}), 500

# if __name__ == '__main__':
#     analysis.run(debug=True)
