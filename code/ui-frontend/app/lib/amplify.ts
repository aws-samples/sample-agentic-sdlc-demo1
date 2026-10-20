import { Amplify } from 'aws-amplify';
import { cognitoUserPoolsTokenProvider } from 'aws-amplify/auth/cognito';
import { sessionStorage } from 'aws-amplify/utils';
import { getConfig } from './config';

export function configureAmplify() {
  const config = getConfig();

  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.cognitoUserPoolId,
        userPoolClientId: config.cognitoClientId,
        identityPoolId: config.cognitoIdentityPoolId,
        loginWith: {
          oauth: {
            domain: config.cognitoDomain,
            scopes: ['openid', 'email', 'profile'],
            redirectSignIn: [window.location.origin],
            redirectSignOut: [window.location.origin],
            responseType: 'code',
          },
        },
      },
    },
  });

  // Store Cognito tokens in sessionStorage (isolated PER BROWSER TAB) instead
  // of the default localStorage (shared across every tab/window of a browser
  // profile — and shared across all incognito windows). Per-tab isolation lets
  // each tab hold a DIFFERENT persona's session, so a demo can run Business
  // Analyst, Solution Architect, Maintenance Specialist, and any future
  // personas side-by-side in a single browser (one tab per persona) — instead
  // of being capped at one normal + one incognito login.
  //
  // Tradeoff: a session lives for the life of its tab — it survives a refresh
  // of the same tab, but a brand-new tab starts signed out and closing the tab
  // ends the session (re-login required). That is the desired behavior for a
  // multi-persona demo.
  cognitoUserPoolsTokenProvider.setKeyValueStorage(sessionStorage);
}
