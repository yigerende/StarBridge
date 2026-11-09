import '@testing-library/jest-dom/vitest';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {Router} from '../router';
import Workspace from './Workspace';

const clients:QueryClient[]=[];
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status});
const prices={membership:{current:{code:'L2',name:'银阶会员',discount:95}},products:[
  {need_oauth:false,standard_price:5,unit_price:4.75,saved:0.25,price_source:'membership'},
  {need_oauth:true,standard_price:8,unit_price:7.6,saved:0.4,price_source:'membership'},
]};
function mount(){
  const q=new QueryClient({defaultOptions:{queries:{retry:false}}});clients.push(q);
  return render(<QueryClientProvider client={q}><Router><Workspace page="register"
    user={{id:'member-price-preview',points:25,unified_account:true}}
    pub={{features:{},orders_open:true,max_quantity:100,price_standard:5,price_oauth:8}}/></Router></QueryClientProvider>);
}
afterEach(()=>{cleanup();clients.splice(0).forEach(q=>q.clear());window.localStorage.clear();vi.restoreAllMocks()});

it('shows membership and both modes using the server quote and exact quantity totals',async()=>{
  vi.spyOn(globalThis,'fetch').mockImplementation(async url=>response(String(url).endsWith('/products')?prices:{regions:[]}));
  mount();await screen.findByText('L2 · 银阶会员');
  const summary=within(screen.getByRole('complementary'));
  expect(summary.getByText('5%')).toBeInTheDocument();
  expect(summary.getByText('4.75 积分')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('spinbutton',{name:'注册数量'}),{target:{value:'3'}});
  expect(summary.getByText('14.25')).toBeInTheDocument();
  expect(summary.getByText('10.75 积分')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/^需要 OAuth/}));
  expect(summary.getByText('22.8')).toBeInTheDocument();
  expect(summary.getByText('2.2 积分')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('spinbutton',{name:'注册数量'}),{target:{value:'4'}});
  expect(screen.getByRole('button',{name:'可用积分不足'})).toBeDisabled();
});

it('blocks new submissions after quote failure and accepts a zero account override on retry',async()=>{
  let failed=true;
  vi.spyOn(globalThis,'fetch').mockImplementation(async url=>String(url).endsWith('/products')
    ? failed?response({error:'unavailable'},503):response({...prices,products:prices.products.map(p=>({...p,unit_price:0,saved:p.standard_price,price_source:'account_override'}))})
    :response({regions:[]}));
  mount();await screen.findByRole('alert');
  expect(screen.getByRole('button',{name:'报价暂不可用'})).toBeDisabled();
  failed=false;fireEvent.click(screen.getByRole('button',{name:'重新获取报价'}));
  await screen.findByText('账户专属单价');
  expect(screen.getByRole('button',{name:'提交注册'})).toBeEnabled();
  expect(within(screen.getByRole('complementary')).getByText('0 积分 / 个')).toBeInTheDocument();
});
