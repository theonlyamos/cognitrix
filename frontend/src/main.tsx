import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { LanguageRoot, applyLanguage, loadLanguage } from './lib/i18n'
import { installExternalLinkHandler } from './lib/externalLinks'

applyLanguage()
// Desktop app: links to other sites open in the default browser.
installExternalLinkHandler()

// Draw the first screen in the person's language, not English then a flash.
void loadLanguage().finally(() => createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LanguageRoot>
      <App />
    </LanguageRoot>
  </StrictMode>,
))
