import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { LanguageRoot, applyLanguage, loadLanguage } from './lib/i18n'

applyLanguage()

// Draw the first screen in the person's language, not English then a flash.
void loadLanguage().finally(() => createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LanguageRoot>
      <App />
    </LanguageRoot>
  </StrictMode>,
))
