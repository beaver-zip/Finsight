import { useState, useEffect } from "react";
import { BASE_URL } from "../api/baseUrl";

export interface FinancialRecord {
  corp_code: string;
  corp_name: string;
  corp_eng_name: string;
  modify_date: string;
  /** 상장사만 값이 있다 (6자리 종목코드) */
  stock_code?: string | null;
}

interface ApiResponse {
  data: FinancialRecord[];
  success: boolean;
}

export const useCorpData = (query: string) => {
  const [data, setData] = useState<FinancialRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!query) return;
    let ignore = false; // 늦게 온 이전 검색 응답이 새 결과·스피너를 덮지 않게 한다

    const fetchData = async () => {
      setLoading(true);
      setError(null);

      try {
        const response = await fetch(
          `${BASE_URL}/financial/corp_list`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ keyword: query }),
          }
        );

        if (!response.ok)
          throw new Error("기업 리스트를 가져오는 데 실패했습니다.");
        const responseData: ApiResponse = await response.json();
        // 결과가 없거나 실패하면 서버는 success=false와 data=[{}]를 준다.
        // 정렬(정확 일치·상장사·짧은 이름 순)은 백엔드 core/dart/corp.py가 한다.
        if (!ignore) setData(responseData.success ? responseData.data : []);
      } catch (err) {
        if (!ignore) setError(
          err instanceof Error ? err.message : "알 수 없는 오류가 발생했습니다."
        );
      } finally {
        if (!ignore) setLoading(false);
      }
    };

    fetchData();
    return () => {
      ignore = true;
    };
  }, [query]);

  return { data, loading, error };
};
