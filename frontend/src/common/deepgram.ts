import { createClient, LiveTranscriptionEvents } from '@deepgram/sdk';
import { DEEPGRAM_API_KEY } from './constants';
import { startMicrophoneStream } from './utils';

const deepgram = createClient(DEEPGRAM_API_KEY);

export async function liveTranscription() {
    let live: any;
    let transcriptCallback: ((data: string) => void) | null = null;

    const onTranscript = (callback: (data: string) => void) => {
        transcriptCallback = callback;
    }
  
    const start = () => {
      live = deepgram.listen.live({
        punctuate: true,
        model: 'nova'
      });
  
      live.addListener(LiveTranscriptionEvents.Open, async () => {
        console.log('Connection to Deepgram established.');
  
        const audioStream = await startMicrophoneStream();
        const mediaRecorder = new MediaRecorder(audioStream);
  
        mediaRecorder.addEventListener('dataavailable', (event) => {
          if (event.data.size > 0 && live && live.getReadyState() === 1) {
            live.send(event.data);
          }
        });
  
        mediaRecorder.start(250);
  
        live.addListener(LiveTranscriptionEvents.Transcript, (data: any) => {
            if (data.channel.alternatives[0].transcript && transcriptCallback) {
                transcriptCallback(data.channel.alternatives[0].transcript);
            }
        });
  
        live.addListener(LiveTranscriptionEvents.Close, () => {
          console.log('Connection to Deepgram closed.');
        });
    
        live.addListener(LiveTranscriptionEvents.Error, (error: any) => {
          console.error('Error:', error);
        });
      });
  
    };
  
    const stop = () => {
      if (live) {
        live.finish();
        live = null;
      }
    };
  
    return { start, stop, onTranscript };
}