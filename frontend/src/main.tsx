import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App'
import Dashboard from './pages/Dashboard'
import Status from './pages/Status'
import ApiKeys from './pages/ApiKeys'
import Learning from './pages/Learning'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/status" element={<Status />} />
          <Route path="/apikeys" element={<ApiKeys />} />
          <Route path="/learning" element={<Learning />} />
        </Routes>
      </App>
    </BrowserRouter>
  </React.StrictMode>,
)