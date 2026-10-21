import { NavLink, Route, Routes } from "react-router-dom";
import { currentRole, setRole, type Role } from "./api";
import { useAsync, Bridge } from "./components";
import { api } from "./api";
import { DashboardPage } from "./pages/Dashboard";
import { EventsPage } from "./pages/Events";
import { EventPage } from "./pages/Event";
import { AlertsPage } from "./pages/Alerts";
import { BriefsPage } from "./pages/Briefs";
import { AbstainPage } from "./pages/Abstain";
import { HealthPage } from "./pages/Health";
import { ReviewPage } from "./pages/Review";
import { ScenePage } from "./pages/Scene";

export function App() {
  const probes = useAsync(() => api.probes().then((payload) => payload.items), [currentRole()]);
  return (
    <div className="shell">
      <aside className="nav">
        <div className="brand">
          <div>
            <h1>稜鏡</h1>
            <p>PRISM BIFRÖST</p>
          </div>
        </div>
        <NavLink to="/" end>態勢</NavLink>
        <NavLink to="/events">事件</NavLink>
        <NavLink to="/review">複核佇列</NavLink>
        <NavLink to="/alerts">告警</NavLink>
        <NavLink to="/briefs">簡報</NavLink>
        <NavLink to="/abstain">棄權</NavLink>
        <NavLink to="/health">探針健康</NavLink>
        <NavLink to="/scenes">場景</NavLink>
        <div className="role">
          <label htmlFor="role">目前角色</label>
          <select
            id="role"
            defaultValue={currentRole()}
            onChange={(event) => {
              setRole(event.target.value as Role);
              window.location.reload();
            }}
          >
            <option value="viewer">檢視者</option>
            <option value="analyst">分析師</option>
            <option value="admin">管理員</option>
          </select>
        </div>
      </aside>
      <main className="main">
        <div className="topbar">
          <div>
            <p>複核工作台</p>
          </div>
          {probes.data && <Bridge probes={probes.data} />}
        </div>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/events/:eventId" element={<EventPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/briefs" element={<BriefsPage />} />
          <Route path="/abstain" element={<AbstainPage />} />
          <Route path="/health" element={<HealthPage />} />
          <Route path="/scenes" element={<ScenePage />} />
          <Route path="/review" element={<ReviewPage />} />
        </Routes>
      </main>
    </div>
  );
}
