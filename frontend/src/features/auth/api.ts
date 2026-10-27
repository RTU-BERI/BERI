import {api, ensureCsrf} from '../../shared/api/client';
import type {LoginInput, RegisterInput, Session, User} from './types';

export const authApi = {
  me: () => api<Session>('/api/auth/me/'),
  async login(input: LoginInput) {await ensureCsrf(); return api<Session>('/api/auth/login/', 'POST', input);},
  async register(input: RegisterInput) {await ensureCsrf(); return api<{user: User}>('/api/auth/register/', 'POST', input);},
  async logout() {await ensureCsrf(); await api<void>('/api/auth/logout/', 'POST', {});},
};
