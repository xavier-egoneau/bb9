import {httpBb9Client} from './bb9-client.js';
import {createBb9Chat} from './chat-ui.js';

const chat = createBb9Chat({
  root: document,
  client: httpBb9Client({apiBase: '/api'}),
  capabilities: {
    canApprove: true,
    canLoadHistory: true,
    canRenderImages: true,
    canUpload: true,
  },
});

chat.start();
