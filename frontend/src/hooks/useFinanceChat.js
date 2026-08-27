import { useCallback, useState } from 'react'
import { postAiChat, streamAiChat } from '../api/client.js'

const MAX_QUESTION_LENGTH = 1000

/**
 * State machine for one finance-assistant conversation turn at a time.
 * The backend contract is authoritative: status completed/partial/failed,
 * facts in tool_results + financial_signals, narrative in answer.
 */
export default function useFinanceChat() {
  const [data, setData] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState(null)

  const ask = useCallback(async (rawQuestion) => {
    const question = String(rawQuestion ?? '').trim()
    if (!question) return
    if (question.length > MAX_QUESTION_LENGTH) {
      setError(
        `Question is too long (${question.length} characters; the limit is ${MAX_QUESTION_LENGTH}).`,
      )
      return
    }
    setIsLoading(true)
    setError(null)
    setData(null)
    let receivedToken = false
    let streamedAnswer = ''
    try {
      const response = await streamAiChat(question, {
        onToken: (token) => {
          receivedToken = true
          streamedAnswer += token
          setData((current) => ({
            ...(current || {}),
            question,
            status: 'partial',
            answer: streamedAnswer,
          }))
        },
      })
      setData(response)
    } catch (err) {
      if (!receivedToken) {
        try {
          setData(await postAiChat(question))
        } catch (fallbackError) {
          setData(null)
          setError(
            fallbackError instanceof Error
              ? fallbackError.message
              : String(fallbackError),
          )
        }
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setIsLoading(false)
    }
  }, [])

  const reset = useCallback(() => {
    setData(null)
    setError(null)
    setIsLoading(false)
  }, [])

  return { data, isLoading, error, ask, reset }
}
