import React from 'react'
import ReactDOM from 'react-dom/client'
import { createHashRouter, RouterProvider } from 'react-router-dom'

import './index.css'
import App from './App'
import ProjectListPage from './pages/ProjectListPage'
import EditorPage from './pages/EditorPage'
import ExportPage from './pages/ExportPage'
import GuidePage from './pages/GuidePage'
import MlOpsPage from './pages/MlOpsPage'

const router = createHashRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <ProjectListPage /> },
      { path: 'projects/:id', element: <EditorPage /> },
      { path: 'export', element: <ExportPage /> },
      { path: 'ml', element: <MlOpsPage /> },
      { path: 'guide', element: <GuidePage /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
