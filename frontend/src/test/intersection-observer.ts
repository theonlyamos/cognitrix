type ObserverRecord = {
  callback: IntersectionObserverCallback;
  options?: IntersectionObserverInit;
  targets: Set<Element>;
};

const observers: ObserverRecord[] = [];

class TestIntersectionObserver implements IntersectionObserver {
  readonly root: Element | Document | null = null;
  readonly rootMargin: string;
  readonly thresholds: ReadonlyArray<number> = [];
  readonly record: ObserverRecord;

  constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
    this.rootMargin = options?.rootMargin ?? '0px 0px 0px 0px';
    this.record = { callback, options, targets: new Set() };
    observers.push(this.record);
  }

  observe(target: Element) { this.record.targets.add(target); }
  unobserve(target: Element) { this.record.targets.delete(target); }
  disconnect() { this.record.targets.clear(); }
  takeRecords(): IntersectionObserverEntry[] { return []; }
}

export function installIntersectionObserver() {
  Object.defineProperty(globalThis, 'IntersectionObserver', {
    configurable: true,
    writable: true,
    value: TestIntersectionObserver,
  });
}

export function uninstallIntersectionObserver() {
  delete (globalThis as { IntersectionObserver?: typeof IntersectionObserver }).IntersectionObserver;
}

export function resetIntersectionObservers() { observers.splice(0); }

export function intersect(target?: Element) {
  for (const observer of observers) {
    const targets = target ? [target] : [...observer.targets];
    for (const observedTarget of targets) {
      if (!observer.targets.has(observedTarget)) continue;
      observer.callback([{ isIntersecting: true, target: observedTarget } as IntersectionObserverEntry], {} as IntersectionObserver);
    }
  }
}

export function observerOptions() { return observers.map(({ options }) => options); }
