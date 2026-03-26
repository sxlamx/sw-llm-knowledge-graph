import React, { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppThemeProvider } from './components/common/ThemeProvider';
import { ErrorBoundary } from './components/common/ErrorBoundary';
import LoadingOverlay from './components/common/LoadingOverlay';
import RequireAuth from './components/auth/RequireAuth';
import Layout from './components/common/Layout';

// Auth pages (redirect-based OAuth flow)
const LoginPage = lazy(() => import('./pages/LoginPage'));
const CallbackPage = lazy(() => import('./pages/CallbackPage'));

// Rich feature pages
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Collection = lazy(() => import('./pages/Collection'));
const Search = lazy(() => import('./pages/Search'));
const GraphViewer = lazy(() => import('./pages/GraphViewer'));
const OntologyEditor = lazy(() => import('./pages/OntologyEditor'));
const Settings = lazy(() => import('./pages/Settings'));
const AgentQuery = lazy(() => import('./pages/AgentQuery'));
const FineTune = lazy(() => import('./pages/FineTune'));

const App: React.FC = () => (
  <ErrorBoundary>
    <AppThemeProvider>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Suspense fallback={<LoadingOverlay message="Loading..." />}>
          <Routes>
            <Route path="/" element={<LoginPage />} />
            <Route path="/auth/callback/google" element={<CallbackPage />} />

            <Route
              path="/dashboard"
              element={
                <RequireAuth>
                  <Layout>
                    <Dashboard />
                  </Layout>
                </RequireAuth>
              }
            />
            <Route
              path="/collection/:id"
              element={
                <RequireAuth>
                  <Layout>
                    <Collection />
                  </Layout>
                </RequireAuth>
              }
            />
            <Route
              path="/search"
              element={
                <RequireAuth>
                  <Layout>
                    <Search />
                  </Layout>
                </RequireAuth>
              }
            />
            <Route
              path="/graph/:collectionId?"
              element={
                <RequireAuth>
                  <GraphViewer />
                </RequireAuth>
              }
            />
            <Route
              path="/ontology/:collectionId"
              element={
                <RequireAuth>
                  <Layout>
                    <OntologyEditor />
                  </Layout>
                </RequireAuth>
              }
            />
            <Route
              path="/settings"
              element={
                <RequireAuth>
                  <Layout>
                    <Settings />
                  </Layout>
                </RequireAuth>
              }
            />
            <Route
              path="/agent/:collectionId"
              element={
                <RequireAuth>
                  <Layout>
                    <AgentQuery />
                  </Layout>
                </RequireAuth>
              }
            />
            <Route
              path="/finetune/:collectionId"
              element={
                <RequireAuth>
                  <Layout>
                    <FineTune />
                  </Layout>
                </RequireAuth>
              }
            />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AppThemeProvider>
  </ErrorBoundary>
);

export default App;
