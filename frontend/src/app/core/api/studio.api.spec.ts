import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { StudioApi } from './studio.api';

 describe('StudioApi', () => {
  let api: StudioApi;
  let http: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    api = TestBed.inject(StudioApi);
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());
  it('preserves unavailable media instead of inventing a preview', () => {
    api.get().subscribe((state) => {
      expect(state.photo_url).toBeNull();
      expect(state.video_url).toBeNull();
      expect(state.media_available).toBeFalse();
    });
    http.expectOne('/api/studio').flush({ photo_url: null, video_url: null, models: [], text_available: false, media_available: false, detail: 'Not configured' });
  });
  it('sends chat only to the authenticated backend, without media or provider keys', () => {
    const messages = [{ role: 'user' as const, content: 'Hello' }];
    api.chat('subscription/model', messages).subscribe((reply) => expect(reply.text).toBe('Hi'));
    const request = http.expectOne('/api/studio/chat');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ model: 'subscription/model', messages });
    request.flush({ text: 'Hi', model: 'subscription/model' });
  });
});
