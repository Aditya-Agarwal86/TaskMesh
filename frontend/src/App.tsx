import React from 'react';
import { Sidebar, NavPage } from './components/Sidebar';
import { Header } from './components/Header';
import { Overview } from './pages/Overview';
import { JobExplorer } from './pages/JobExplorer';
import { JobSubmit } from './pages/JobSubmit';
import { JobDetail } from './pages/JobDetail';
import { WorkersQueue } from './pages/WorkersQueue';
import { Job } from './types/api';
import { api } from './lib/api';
import { useEventStream } from './hooks/useEventStream';

export const App: React.FC = () => {
  const [currentPage, setCurrentPage] = React.useState<NavPage>('overview');
  const [selectedJobId, setSelectedJobId] = React.useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = React.useState<boolean>(false);
  const [lastUpdated, setLastUpdated] = React.useState<Date>(new Date());
  const [serviceStatus, setServiceStatus] = React.useState<string>('healthy');
  const [activeWorkerCount, setActiveWorkerCount] = React.useState<number>(0);
  const [refreshTrigger, setRefreshTrigger] = React.useState<number>(0);

  const fetchHealth = React.useCallback(async () => {
    try {
      const [h, metrics] = await Promise.all([
        api.getHealth().catch(() => null),
        api.getMetrics().catch(() => null),
      ]);
      if (h && h.status === 'ok') {
        setServiceStatus('healthy');
      } else {
        setServiceStatus('degraded');
      }
      if (metrics) {
        setActiveWorkerCount(metrics.active_workers_count || metrics.queue_depth?.processing || 0);
      }
      setLastUpdated(new Date());
    } catch {
      setServiceStatus('unreachable');
    }
  }, []);

  // Hook up real-time SSE stream with automatic reconnection and polling fallback
  const { status: streamStatus, lastEventTime, reconnect } = useEventStream({
    enabled: true,
    onJobUpdate: () => {
      // Incremental event received: bump refresh trigger to update tables and cards
      setRefreshTrigger((prev) => prev + 1);
    },
    onRefreshNeeded: () => {
      fetchHealth();
      setRefreshTrigger((prev) => prev + 1);
    },
  });

  // Polling fallback when streaming is in fallback mode or periodically every 10s for health
  React.useEffect(() => {
    fetchHealth();
    const intervalTime = streamStatus === 'polling_fallback' ? 4000 : 10000;
    const interval = setInterval(() => {
      fetchHealth();
      if (streamStatus === 'polling_fallback') {
        setRefreshTrigger((prev) => prev + 1);
      }
    }, intervalTime);
    return () => clearInterval(interval);
  }, [fetchHealth, streamStatus]);

  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    try {
      await fetchHealth();
      setRefreshTrigger((prev) => prev + 1);
      setLastUpdated(new Date());
    } finally {
      setIsRefreshing(false);
    }
  };


  const handleSelectJob = (job: Job) => {
    setSelectedJobId(job.id);
  };

  const handleBackFromDetail = () => {
    setSelectedJobId(null);
  };

  const getPageTitle = () => {
    if (selectedJobId) return `Job Details: ${selectedJobId.substring(0, 8)}...`;
    switch (currentPage) {
      case 'overview':
        return 'System Overview & Telemetry';
      case 'explorer':
        return 'Job Explorer & History';
      case 'submit':
        return 'Submit Distributed Task';
      case 'workers':
        return 'Workers & Queue Telemetry';
      default:
        return 'TaskMesh Dashboard';
    }
  };

  interface ExplorerFilters {
    status?: string;
    priority?: string;
    taskType?: string;
    searchId?: string;
  }
  const [explorerFilters, setExplorerFilters] = React.useState<ExplorerFilters>({});

  const handleNavigateExplorer = (status?: string) => {
    setSelectedJobId(null);
    setExplorerFilters((prev) => ({
      ...prev,
      status: status || '',
    }));
    setCurrentPage('explorer');
  };

  return (
    <div className="app-container">
      <Sidebar
        currentPage={currentPage}
        onNavigate={(page) => {
          setSelectedJobId(null);
          setCurrentPage(page);
        }}
        serviceStatus={serviceStatus}
        workerCount={activeWorkerCount}
      />

      <main className="main-content">
        <Header
          title={getPageTitle()}
          onRefresh={handleManualRefresh}
          isRefreshing={isRefreshing}
          lastUpdated={lastUpdated}
          streamStatus={streamStatus}
          lastEventTime={lastEventTime}
          onReconnect={reconnect}
        />

        <div className="content-body">
          {selectedJobId ? (
            <JobDetail jobId={selectedJobId} onBack={handleBackFromDetail} />
          ) : (
            <>
              {currentPage === 'overview' && (
                <Overview
                  onSelectJob={handleSelectJob}
                  onNavigateExplorer={handleNavigateExplorer}
                  refreshTrigger={refreshTrigger}
                />
              )}
              {currentPage === 'explorer' && (
                <JobExplorer
                  onSelectJob={handleSelectJob}
                  savedFilters={explorerFilters}
                  onUpdateFilters={setExplorerFilters}
                  refreshTrigger={refreshTrigger}
                />
              )}
              {currentPage === 'submit' && (
                <JobSubmit onJobCreated={handleSelectJob} />
              )}
              {currentPage === 'workers' && (
                <WorkersQueue
                  onSelectJob={handleSelectJob}
                  refreshTrigger={refreshTrigger}
                />
              )}
            </>
          )}
        </div>
      </main>
    </div>
  );
};

export default App;
