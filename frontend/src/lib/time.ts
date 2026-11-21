import { friendlyTime } from '@/lib/plain';
/** A server time (naive UTC) as the person reads it: "9:00", "tomorrow 9:00", "Mon 23 Nov, 9:00". */
export const utcToLocal = (s: string) => friendlyTime(s);
