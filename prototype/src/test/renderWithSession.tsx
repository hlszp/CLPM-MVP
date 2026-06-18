import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { AppSessionProvider } from '../app/session/AppSessionContext';

export function renderWithSession(ui: ReactElement, initialEntries = ['/']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AppSessionProvider>{ui}</AppSessionProvider>
    </MemoryRouter>
  );
}
