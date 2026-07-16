import "@/index.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import Layout from "@/components/Layout";
import Dashboard from "@/pages/Dashboard";
import LiveEvents from "@/pages/LiveEvents";
import EventLog from "@/pages/EventLog";
import DVR from "@/pages/DVR";
import People from "@/pages/People";
import SentinelAI from "@/pages/SentinelAI";
import Rules from "@/pages/Rules";
import Settings from "@/pages/Settings";

function App() {
  return (
    <BrowserRouter>
      <Toaster theme="dark" position="top-right" />
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/live" element={<LiveEvents />} />
          <Route path="/events" element={<EventLog />} />
          <Route path="/dvr" element={<DVR />} />
          <Route path="/people" element={<People />} />
          <Route path="/sentinel" element={<SentinelAI />} />
          <Route path="/rules" element={<Rules />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
