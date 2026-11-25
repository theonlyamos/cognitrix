import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AppIcon } from './InstallApp';

describe('AppIcon', () => {
  it("shows the app's own icon, and its initials when it has none or it doesn't load", () => {
    const { container, rerender } = render(<AppIcon title="Team dashboard" slug="team-dashboard" src="/app-icons/team-dashboard?t=x&v=1" />);
    const img = container.querySelector('img');
    expect(img).toHaveAttribute('src', '/app-icons/team-dashboard?t=x&v=1');

    fireEvent.error(img!);
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toBe('TD');

    // A new icon (another version) is tried again.
    rerender(<AppIcon title="Team dashboard" slug="team-dashboard" src="/app-icons/team-dashboard?t=x&v=2" />);
    expect(container.querySelector('img')).not.toBeNull();

    rerender(<AppIcon title="Team dashboard" slug="team-dashboard" />);
    expect(container.textContent).toBe('TD');
  });
});
