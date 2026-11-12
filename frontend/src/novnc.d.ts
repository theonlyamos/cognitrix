declare module '@novnc/novnc/lib/rfb' {
  interface RFBOptions {
    credentials?: { password?: string; username?: string; target?: string };
    shared?: boolean;
    wsProtocols?: string[];
  }
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, url: string, options?: RFBOptions);
    viewOnly: boolean;
    scaleViewport: boolean;
    resizeSession: boolean;
    background: string;
    disconnect(): void;
    focus(): void;
  }
}
