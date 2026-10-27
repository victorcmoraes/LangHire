import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import EasyApplyPanel, { EasyAnswers } from './EasyApplyPanel';
import * as api from '../lib/api';
vi.mock('./HumanInterventions',()=>({default:()=>null}));
vi.mock('../lib/api',()=>({getEasyAnswers:vi.fn(),getEasyReviews:vi.fn(),approveEasyAnswer:vi.fn(),proposeEasyAnswer:vi.fn(),
  getEasyChannel:vi.fn(),verifyEasySession:vi.fn(),startApplying:vi.fn(),getJobs:vi.fn(),getEasyFacts:vi.fn(),qualifyEasyJob:vi.fn(),getApplyStatus:vi.fn()}));
beforeEach(()=>{
 vi.resetAllMocks();
 vi.mocked(api.getEasyAnswers).mockResolvedValue([]);vi.mocked(api.getEasyReviews).mockResolvedValue([]);
 vi.mocked(api.getJobs).mockResolvedValue([]);vi.mocked(api.getEasyFacts).mockResolvedValue({});
 vi.mocked(api.getEasyChannel).mockResolvedValue({suspended:0,reason:''});
 vi.mocked(api.getApplyStatus).mockResolvedValue({running:false} as never);
});
describe('Easy Apply controls',()=>{
 it('confirms salary units next to the answer before saving',async()=>{
  vi.mocked(api.getEasyAnswers).mockResolvedValue([{id:1,question:'Salary?',state:'pending',answer:null,proposal:null,context:{country:'BR'},refs:[],jobs:['job'],field:{type:'text'}}]);
  vi.mocked(api.approveEasyAnswer).mockResolvedValue({});
  render(<EasyAnswers/>);
  await userEvent.type(await screen.findByRole('textbox',{name:'Salary?'}),'7000');
  const confirm=screen.getByRole('button',{name:'Confirmar e reutilizar'});
  expect(confirm).toBeDisabled();
  await userEvent.type(screen.getByRole('textbox',{name:'Moeda: Salary?'}),'BRL');
  await userEvent.selectOptions(screen.getByRole('combobox',{name:'Período: Salary?'}),'monthly');
  await userEvent.click(confirm);
  await waitFor(()=>expect(api.approveEasyAnswer).toHaveBeenCalledWith(1,'7000',{currency:'BRL',period:'monthly'}));
 });
 it('shows model abstention as missing information instead of an API error',async()=>{
  vi.mocked(api.getEasyAnswers).mockResolvedValue([{id:1,question:'Years?',state:'pending',answer:null,proposal:null,context:{},refs:[],jobs:['job'],field:{type:'number'}}]);
  vi.mocked(api.proposeEasyAnswer).mockResolvedValue({supported:false,message:'Faltam fatos no cadastro.'});
  render(<EasyAnswers/>);
  await userEvent.click(await screen.findByRole('button',{name:'Propor com 8b'}));
  expect(await screen.findByRole('status')).toHaveTextContent('Faltam fatos no cadastro.');
  expect(api.approveEasyAnswer).not.toHaveBeenCalled();
 });
 it('does not use a model proposal until the human approves it',async()=>{
  vi.mocked(api.getEasyAnswers).mockResolvedValue([{id:1,question:'Availability?',state:'proposed',answer:null,proposal:'Immediate',context:{country:'BR'},refs:['availability'],jobs:['job'],field:{type:'text'}}]);
  vi.mocked(api.approveEasyAnswer).mockResolvedValue({});
  render(<EasyAnswers/>);
  const button=await screen.findByRole('button',{name:'Confirmar e reutilizar'});
  expect(api.approveEasyAnswer).not.toHaveBeenCalled();expect(api.startApplying).not.toHaveBeenCalled();
  await userEvent.click(button);
  await waitFor(()=>expect(api.approveEasyAnswer).toHaveBeenCalledWith(1,'Immediate'));
  expect(api.startApplying).not.toHaveBeenCalled();
 });
 it('releases only the selected review and explicitly uses Easy Apply submit',async()=>{
  vi.mocked(api.getEasyReviews).mockResolvedValue([{id:'review-123',job_url:'https://www.linkedin.com/jobs/view/123/',digest:'digest',payload:JSON.stringify({title:'Backend',company:'Fixture',answers:[],resume_hash:'hash',review_text:'Reviewed'})}]);
  vi.mocked(api.startApplying).mockResolvedValue({success:true,message:'Started'});
  render(<EasyApplyPanel/>);
  await userEvent.click(await screen.findByRole('button',{name:'Conferi esta revisão: enviar uma vez'}));
  await waitFor(()=>expect(api.startApplying).toHaveBeenCalledWith({mode:'easy',execution:'submit',job_url:'https://www.linkedin.com/jobs/view/123/',review_id:'review-123',workers:1}));
 });
});
