import React from 'react';
import { createRoot } from 'react-dom/client';
import '@xyflow/react/dist/style.css';
import './styles.css';
import { Galaxy } from './Galaxy';
createRoot(document.getElementById('root')!).render(<React.StrictMode><Galaxy/></React.StrictMode>);