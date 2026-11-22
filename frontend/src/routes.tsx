import type { RouteObject } from 'react-router-dom'

import App from './App'
import ErrorPage from './pages/ErrorPage'
import ProjectListPage from './pages/ProjectListPage'
import EditorPage from './pages/EditorPage'
import ExportPage from './pages/ExportPage'
import GuidePage from './pages/GuidePage'
import MlOpsPage from './pages/MlOpsPage'
import AnalysisPage from './pages/AnalysisPage'
import CircuitPage from './pages/CircuitPage'

/** 画面のルート。描画の例外・存在しない URL は日本語のエラー画面（ErrorPage）で受ける。 */
export const routes: RouteObject[] = [
  {
    path: '/',
    element: <App />,
    errorElement: <ErrorPage />,
    children: [
      {
        // ページの中で落ちても、サイドバーは残してエラー画面を出す
        errorElement: <ErrorPage />,
        children: [
          { index: true, element: <ProjectListPage /> },
          { path: 'projects/:id', element: <EditorPage /> },
          { path: 'projects/:id/analysis', element: <AnalysisPage /> },
          { path: 'export', element: <ExportPage /> },
          { path: 'ml', element: <MlOpsPage /> },
          { path: 'circuit', element: <CircuitPage /> },
          { path: 'guide', element: <GuidePage /> },
          { path: '*', element: <ErrorPage notFound /> },
        ],
      },
    ],
  },
]
