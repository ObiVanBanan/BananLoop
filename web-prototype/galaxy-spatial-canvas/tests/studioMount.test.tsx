// @vitest-environment jsdom
import React from 'react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {Studio} from '../src/Studio';
import {discussStudio} from '../src/api/client';

vi.mock('../src/api/client',()=>({
  discussStudio:vi.fn(),
  confirmStudioLaunch:vi.fn(),
  launchStudio:vi.fn(),
  getStudioLaunch:vi.fn(),
}));
const mockDiscuss=vi.mocked(discussStudio);
let counter=0;

beforeEach(()=>{
  window.localStorage.clear();
  mockDiscuss.mockReset();
  counter=0;
  if(!globalThis.crypto.randomUUID){
    Object.defineProperty(globalThis.crypto,'randomUUID',{configurable:true,value:()=>`chat-${++counter}-0000-0000-0000-000000000000`});
  }
  // Reproduce the user-reported browser behavior: scrollIntoView returns a Promise.
  Object.defineProperty(HTMLElement.prototype,'scrollIntoView',{
    configurable:true,value:()=>Promise.resolve(),
  });
});
afterEach(()=>{cleanup()});

describe('Studio chat desktop runtime',()=>{
  it('mounts and unmounts without React trying to call a Promise as effect cleanup',()=>{
    const {unmount}=render(<Studio token="" onBack={()=>{}} onOpenCampaign={()=>{}}/>);
    expect(screen.getByText('С чего начнём?')).toBeTruthy();
    expect(()=>unmount()).not.toThrow();
  });

  it('opens multiple blank chats and can return to an earlier chat',()=>{
    render(<Studio token="" onBack={()=>{}} onOpenCampaign={()=>{}}/>);
    fireEvent.click(screen.getByRole('button',{name:'Новый чат'}));
    fireEvent.click(screen.getByRole('button',{name:'Новый чат'}));
    const threads=screen.getAllByRole('button',{name:/^◷\s*Новый чат$/});
    expect(threads).toHaveLength(2);
    fireEvent.click(threads[1]);
    expect(screen.getByText('С чего начнём?')).toBeTruthy();
  });

  it('can choose Supervised before sending a first message without launching Engine',()=>{
    render(<Studio token="" onBack={()=>{}} onOpenCampaign={()=>{}}/>);
    const supervised=screen.getByRole('button',{name:'Supervised'});
    fireEvent.click(supervised);
    expect(supervised.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText('С чего начнём?')).toBeTruthy();
    expect(mockDiscuss).not.toHaveBeenCalled();
  });

  it('sends a real request, surfaces failures and retries without fabricating a response',async()=>{
    mockDiscuss.mockRejectedValueOnce(new Error('Codex app-server недоступен'));
    mockDiscuss.mockResolvedValueOnce({reply:'Давай определим метрику.',draft:null,provider:'codex_cli',model_called:true});
    render(<Studio token="" onBack={()=>{}} onOpenCampaign={()=>{}}/>);
    fireEvent.change(screen.getByRole('textbox',{name:'Сообщение в чате'}),{target:{value:'Улучшим RAG-поиск'}});
    fireEvent.click(screen.getByRole('button',{name:'Отправить'}));
    await waitFor(()=>expect(screen.getByText('Не удалось получить ответ')).toBeTruthy());
    expect(mockDiscuss).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button',{name:'Повторить'}));
    await waitFor(()=>expect(screen.getByText('Давай определим метрику.')).toBeTruthy());
    expect(mockDiscuss).toHaveBeenCalledTimes(2);
    expect(mockDiscuss.mock.calls[1][1].message).toBe('Улучшим RAG-поиск');
    expect(document.querySelectorAll('.studio-transcript-turn.turn-user')).toHaveLength(1);
  });
});