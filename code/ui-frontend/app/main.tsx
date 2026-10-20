import React from 'react';
import ReactDOM from 'react-dom/client';
import { loadConfig } from './lib/config';
import { configureAmplify } from './lib/amplify';
import App from './app';
import '../styles/index.css';

// Load runtime config, then configure Amplify, then render
loadConfig().then(() => {
  configureAmplify();
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
