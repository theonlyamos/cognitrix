import os
from threading import Thread
from dotenv import load_dotenv
from multiprocessing import Pool
from typing import Callable, Optional

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone,
    SpeakOptions
)

from pydub import AudioSegment
from pydub.playback import play

load_dotenv()

class Transcriber:
    def __init__(self, api_key: Optional[str] = None, on_message_callback: Optional[Callable[[str, 'Transcriber'], None]]=None):
        self.api_key = api_key if api_key else os.getenv('DEEPGRAM_API_KEY', '')
        self.client = None
        self.connection = None
        self.microphone = None
        self.on_message_callback = on_message_callback
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
    
    def text_to_speech(self, text: str, filename: str = "output.mp3"):
        try:
            if self.client:
                speak_options = SpeakOptions(
                    model="aura-asteria-en",  # Customize the model as needed
                )
                response = self.client.speak.v("1").save(filename, {"text": text}, speak_options)
                # print(f"Audio content written to file '{filename}'")
                # print(response.to_json(indent=4))

                speech_thread = Thread(target=self.play_audio, args=(filename,))
                speech_thread.daemon = True
                speech_thread.start()

        except Exception as e:
            print(f"Exception in text_to_speech: {e}")

    def play_audio(self, filename: str):
        # Load the MP3 file
        audio = AudioSegment.from_mp3(filename)
        # Play the audio file
        play(audio)

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
