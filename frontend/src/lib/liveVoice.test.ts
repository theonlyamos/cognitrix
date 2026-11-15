import { describe, expect, it } from 'vitest';
import {
  approvalSpeech,
  isExitCommand,
  isStopCommand,
  matchOption,
  parseApprovalAnswer,
  questionAnswer,
  questionSpeech,
} from './liveVoice';

describe('parseApprovalAnswer', () => {
  it('reads plain yes and no', () => {
    expect(parseApprovalAnswer('Yes.')).toBe('yes');
    expect(parseApprovalAnswer('yeah go ahead')).toBe('yes');
    expect(parseApprovalAnswer('No thanks')).toBe('no');
    expect(parseApprovalAnswer("Don't.")).toBe('no');
    expect(parseApprovalAnswer('oui')).toBe('yes');
  });

  it('lets a no win over a yes in the same breath', () => {
    expect(parseApprovalAnswer('yes no')).toBe('no');
    expect(parseApprovalAnswer('okay stop')).toBe('no');
  });

  it('asks again for long or unclear answers', () => {
    expect(parseApprovalAnswer('yes but first check the folder it writes to please')).toBeNull();
    expect(parseApprovalAnswer('what does it do')).toBeNull();
    expect(parseApprovalAnswer('')).toBeNull();
    // "know" and "yesterday" must not count as no and yes.
    expect(parseApprovalAnswer('I know')).toBeNull();
    expect(parseApprovalAnswer('yesterday')).toBeNull();
  });

  it('needs approve or allow for high-risk actions', () => {
    expect(parseApprovalAnswer('yes', 'high')).toBeNull();
    expect(parseApprovalAnswer('okay', 'high')).toBeNull();
    expect(parseApprovalAnswer('approve', 'high')).toBe('yes');
    expect(parseApprovalAnswer('yes, allow it', 'high')).toBe('yes');
    expect(parseApprovalAnswer('no', 'high')).toBe('no');
  });
});

describe('commands', () => {
  it('spots a stop', () => {
    expect(isStopCommand('Stop.')).toBe(true);
    expect(isStopCommand('stop please')).toBe(true);
    expect(isStopCommand('stop using tabs in the file')).toBe(false);
  });

  it('spots leaving voice mode', () => {
    expect(isExitCommand('End voice mode.')).toBe(true);
    expect(isExitCommand('goodbye')).toBe(true);
    expect(isExitCommand('write a goodbye email')).toBe(false);
  });
});

describe('questions', () => {
  const options = [
    { id: 'a', label: 'Blue' },
    { id: 'b', label: 'Blue green' },
    { id: 'c', label: 'Red' },
  ];

  it('matches options by words or number', () => {
    expect(matchOption('red', options)).toBe('c');
    expect(matchOption('the blue green one', options)).toBe('b');
    expect(matchOption('option two', options)).toBe('b');
    expect(matchOption('the third', options)).toBe('c');
    expect(matchOption('red or blue', options)).toBeNull();
    expect(matchOption('option nine', options)).toBeNull();
  });

  it('falls back to free text only when allowed', () => {
    const q = { request_id: 'q', prompt: 'Which colour?', options, allow_free_text: false };
    expect(questionAnswer('purple', q)).toBeNull();
    expect(questionAnswer('purple', { ...q, allow_free_text: true })).toEqual({ text: 'purple' });
    expect(questionAnswer('red', q)).toEqual({ option_id: 'c' });
  });

  it('never answers sign-in requests by voice', () => {
    const q = { request_id: 'q', prompt: 'Token?', options: [], allow_free_text: true, secret: { name: 'x' } };
    expect(questionAnswer('abc123', q)).toBeNull();
    expect(questionSpeech(q)).toMatch(/left it on screen/);
  });

  it('reads the options out', () => {
    const text = questionSpeech({ request_id: 'q', prompt: 'Which colour', options, allow_free_text: false });
    expect(text).toBe('Which colour. The options are: 1, Blue; 2, Blue green; 3, Red. Say the one you want.');
  });
});

describe('approvalSpeech', () => {
  it('says the command and how to answer', () => {
    const text = approvalSpeech({ request_id: 'r', tool_name: 'bash', params: { command: 'rm -rf build' }, risk_level: 'medium' }, 'Coder');
    expect(text).toBe('Coder wants to run bash. The command is: rm -rf build. Say yes to allow it, or no to deny it.');
  });

  it('shortens long commands and flags high risk', () => {
    const text = approvalSpeech({ request_id: 'r', tool_name: 'delete_files', params: { command: 'x '.repeat(200) }, risk_level: 'high' });
    expect(text.startsWith('The agent wants to run delete files.')).toBe(true);
    expect(text).toMatch(/and more\./);
    expect(text).toMatch(/Say approve/);
  });
});
