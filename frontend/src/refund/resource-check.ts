export type SeatCount = {total:number;remaining:number;used:number;held:number};
export type ResourceCheck = {
 valid:boolean;http_status:number;code:string;message:string;checked_at:string;latency_ms:number;
 ip_address?:string;seats?:{standard?:SeatCount;premium?:SeatCount};capacity_message?:string;
};
