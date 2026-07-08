import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export const api = axios.create({ baseURL: API });

export const getHealth = () => api.get("/system/health").then((r) => r.data);
export const getSettings = () => api.get("/settings").then((r) => r.data);
export const updateSettings = (patch) => api.put("/settings", patch).then((r) => r.data);
export const getCameras = () => api.get("/cameras").then((r) => r.data);
export const getEvents = (params) => api.get("/events", { params }).then((r) => r.data);
export const saveEvent = (id, saved) => api.patch(`/events/${id}/save`, null, { params: { saved } }).then((r) => r.data);
export const getSegments = (params) => api.get("/dvr/segments", { params }).then((r) => r.data);
export const getPeople = () => api.get("/people").then((r) => r.data);
export const createPerson = (data) => api.post("/people", data).then((r) => r.data);
export const updatePerson = (id, data) => api.put(`/people/${id}`, data).then((r) => r.data);
export const deletePerson = (id) => api.delete(`/people/${id}`).then((r) => r.data);
export const getRules = () => api.get("/rules").then((r) => r.data);
export const createRule = (data) => api.post("/rules", data).then((r) => r.data);
export const updateRule = (id, data) => api.put(`/rules/${id}`, data).then((r) => r.data);
export const deleteRule = (id) => api.delete(`/rules/${id}`).then((r) => r.data);
export const askAssistant = (session_id, message) => api.post("/assistant/chat", { session_id, message }).then((r) => r.data);
export const getHistory = (session_id) => api.get("/assistant/history", { params: { session_id } }).then((r) => r.data);
