cd /Users/zhixinli/Workspace/ADCdb/ADCpatent/ADC_Patents
while true; do
  date
  python3 scripts/09_biweekly_monitor.py \
    --query-start 0 \
    --query-end 216 \
    --max-results-per-query 100 \
    --pages-per-query 10 \
    --skip-detail-fetch \
    --skip-downloads \
    --page-delay 8 \
    --retries 0 \
    --max-failed-queries 1
  echo "sleeping 6 hour..."
  sleep 21600
done

