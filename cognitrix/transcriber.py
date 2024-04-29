import os
from dotenv import load_dotenv
from multiprocessing import Pool
from typing import Callable, Optional

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone
)

import pyttsx3

load_dotenv()

class Transcriber:
    def __init__(self, api_key: Optional[str] = None, on_message_callback: Optional[Callable[[str, 'Transcriber'], None]]=None):
        self.api_key = api_key if api_key else os.getenv('DEEPGRAM_API_KEY', '')
        self.client = None
        self.connection = None
        self.microphone = None
        self.on_message_callback = on_message_callback
        self.tts_engine = pyttsx3.init()
        self.pool = Pool(processes=2)

    def on_message(self, connection, result, **kwargs):
        sentence = result.channel.alternatives[0].transcript
        if len(sentence) == 0:
            return
        print(f"\nUser: {sentence}")
        if self.on_message_callback:
            self.on_message_callback(sentence, self)

    def on_error(self, connection, error, **kwargs):
        print(f"\n\n{error}\n\n")

    def setup_client(self):
        config = DeepgramClientOptions(options={"keepalive": "true"})
        self.client = DeepgramClient(self.api_key, config)
    
    def text_to_speech(self, text: str):
        self.pool.apply_async(self.run_tts, (text,))
    
    @staticmethod
    def run_tts(text):
        try:
            tts_engine = pyttsx3.init()
            tts_engine.say(text)
            tts_engine.runAndWait()
            tts_engine.stop()
        except Exception as e:
            print(f"Exception in run_tts: {e}")

    def start_transcription(self):
        self.setup_client()
        if self.client:
            self.connection = self.client.listen.live.v("1")
            self.connection.on(LiveTranscriptionEvents.Transcript, self.on_message)
            self.connection.on(LiveTranscriptionEvents.Error, self.on_error)

            options = LiveOptions(
                model="nova-2",
                punctuate=True,
                language="en-US",
                encoding="linear16",
                channels=1,
                sample_rate=16000,
            )

            if self.connection.start(options) is False:
                print("Failed to connect to Deepgram")
                return False

            self.microphone = Microphone(self.connection.send)
            self.microphone.start()
            return True

    def stop_transcription(self):
        if self.microphone:
            self.microphone.finish()
        if self.connection:
            self.connection.finish()
        print("Transcription stopped.")

def main():
    api_key = os.getenv('DEEPGRAM_API_KEY', '')
    transcriber = Transcriber(api_key)
    if transcriber.start_transcription():
        input("\n\nPress Enter to stop recording...\n\n")
        transcriber.stop_transcription()

if __name__ == "__main__":
    main()
