import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App'
import Dashboard from './pages/Dashboard'
import ApiKeys from './pages/ApiKeys'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/apikeys" element={<ApiKeys />} />
        </Routes>
      </App>
    </BrowserRouter>
  </React.StrictMode>,
)
