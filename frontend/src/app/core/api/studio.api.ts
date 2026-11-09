import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';

export interface StudioState {
  photo_url: string | null;
  video_url: string | null;
  models: string[];
  text_available: boolean;
  media_available: boolean;
  detail: string;
}
export interface StudioMessage { role: 'user' | 'assistant'; content: string }

@Injectable({ providedIn: 'root' })
export class StudioApi {
  private readonly http = inject(HttpClient);
  get() { return this.http.get<StudioState>('/api/studio'); }
  chat(model: string, messages: StudioMessage[]) {
    return this.http.post<{ text: string; model: string }>('/api/studio/chat', { model, messages });
  }
}
